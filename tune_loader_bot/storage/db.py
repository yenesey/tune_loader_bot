"""Слой доступа к SQLite через aiosqlite.

Соединение живёт в экземпляре (а не в атрибуте класса), путь задаётся явно,
есть `close()`, а схема приводится миграциями (`storage/migrations.py`).

Методы работают с доменными моделями (`Download`, `Subscription`); для
dict-совместимости есть `Download.coerce`/`to_legacy_dict`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

import aiosqlite

from tune_loader_bot.domain.models import Download, Subscription
from tune_loader_bot.storage import migrations

DEFAULT_DB_PATH = "downloads.db"

DOWNLOAD_COLUMNS = (
    "date, user_id, url, video, file_name, file_size, title, artist, channel, delivered"
)
SUBSCRIPTION_COLUMNS = "id, user_id, url, keywords, interval, last_check"

SAVE_DOWNLOAD = """
INSERT INTO downloads(date, user_id, url, video, file_name, file_size, title, artist, channel, delivered)
VALUES(:date, :user_id, :url, :video, :file_name, :file_size, :title, :artist, :channel, :delivered)
ON CONFLICT(url, video, user_id) DO UPDATE SET
    date = excluded.date,
    file_name = excluded.file_name,
    file_size = excluded.file_size,
    title = excluded.title,
    artist = excluded.artist,
    channel = excluded.channel,
    delivered = MAX(COALESCE(downloads.delivered, 0), COALESCE(excluded.delivered, 0))
"""

SAVE_SUBSCRIPTION = """
INSERT INTO subscriptions(id, user_id, url, keywords, interval, last_check)
VALUES(:id, :user_id, :url, :keywords, :interval, :last_check)
ON CONFLICT(id) DO UPDATE SET
    user_id = excluded.user_id,
    url = excluded.url,
    keywords = excluded.keywords,
    interval = excluded.interval,
    last_check = excluded.last_check
"""


class Storage:
    """Соединение с базой и операции над доменными моделями."""

    def __init__(self, connection: aiosqlite.Connection, path: Path) -> None:
        self._connection = connection
        self.path = path

    @property
    def connection(self) -> aiosqlite.Connection:
        """Прямой доступ к соединению (инструменты, отчёты, тесты)."""
        return self._connection

    @classmethod
    async def open(
        cls,
        path: str | Path = DEFAULT_DB_PATH,
        *,
        auto_migrate: bool = True,
        backup: bool = True,
    ) -> Storage:
        """Открывает базу, включает WAL и (по умолчанию) применяет миграции.

        WAL — persistent-свойство файла: включив его один раз, база продолжает
        работать в этом режиме.
        """
        db_path = Path(path)
        connection = await aiosqlite.connect(db_path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 5000")
        cursor = await connection.execute("PRAGMA journal_mode = WAL")
        journal_mode = (await cursor.fetchone())[0]
        await connection.execute("PRAGMA synchronous = NORMAL")

        storage = cls(connection, db_path)
        logging.info("база %s открыта (journal_mode=%s)", db_path, journal_mode)
        if auto_migrate:
            report = await storage.migrate(backup=backup)
            if report.applied:
                logging.info("схема обновлена:\n%s", report.as_text())
        return storage

    async def migrate(self, *, backup: bool = True, apply: bool = True) -> migrations.MigrationReport:
        return await migrations.migrate(self._connection, path=self.path, backup=backup, apply=apply)

    async def close(self) -> None:
        await self._connection.close()

    # --- downloads ---------------------------------------------------------

    async def save_download(self, download: Download | Mapping[str, Any]) -> Download:
        """Вставляет или полностью обновляет запись (D8: раньше обновлялся только delivered)."""
        model = Download.coerce(download)
        await self._connection.execute(SAVE_DOWNLOAD, model.as_row())
        await self._connection.commit()
        return model

    async def find_download(
        self, url: str, video: bool = False, user_id: int | None = None
    ) -> Download | None:
        """Ищет запись по ссылке.

        `user_id=None` — «любой пользователь»; выбор детерминирован: самая
        свежая запись, а не произвольная строка. Проверка файла на диске —
        в `MediaLibrary.find`.
        """
        sql = f"SELECT {DOWNLOAD_COLUMNS} FROM downloads WHERE url = ? AND video = ?"
        params: list[Any] = [url, int(video)]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(int(user_id))
        sql += " ORDER BY COALESCE(date, '') DESC, rowid DESC LIMIT 1"

        cursor = await self._connection.execute(sql, params)
        row = await cursor.fetchone()
        return Download.from_row(row) if row else None

    async def count_downloads(self) -> int:
        cursor = await self._connection.execute("SELECT COUNT(*) FROM downloads")
        return int((await cursor.fetchone())[0])

    # --- subscriptions -----------------------------------------------------

    async def subscriptions(self) -> list[Subscription]:
        cursor = await self._connection.execute(
            f"SELECT {SUBSCRIPTION_COLUMNS} FROM subscriptions ORDER BY id"
        )
        return [Subscription.from_row(row) for row in await cursor.fetchall()]

    async def save_subscription(
        self, subscription: Subscription | Mapping[str, Any]
    ) -> Subscription:
        model = Subscription.coerce(subscription)
        cursor = await self._connection.execute(SAVE_SUBSCRIPTION, model.as_row())
        if model.id is None:
            model.id = cursor.lastrowid
        await self._connection.commit()
        return model

    async def save_subscriptions(self, subscriptions: Iterable[Subscription]) -> None:
        for subscription in subscriptions:
            await self.save_subscription(subscription)
