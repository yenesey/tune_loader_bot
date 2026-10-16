"""Миграции схемы SQLite.

Живая база (`downloads.db`, 2065 записей на 16.10.2026) содержит реальные
данные, поэтому:

* текущая версия схемы хранится в `PRAGMA user_version` (учёта раньше не было
  вовсе — было 0);
* перед применением миграций к **существующему** файлу рядом кладётся копия
  (`downloads.db.YYYYmmdd-HHMMSS.bak`, покрыта `.gitignore`);
* все шаги идемпотентны: повторный запуск ничего не ломает;
* миграция «с нуля» (файла нет) не требует бэкапа.

Версия 1 чинит известные дефекты D1/D2/D8:

* D1 — `CREATE TABLE` в коде не содержал `artist`/`channel`/`delivered`, хотя
  код их пишет и читает: чистая база не поднималась;
* D2 — `user_id` мог быть NULL, а NULL в SQLite не участвует в UNIQUE-индексе:
  канальные посты копились дублями. Теперь NULL → 0 и уникальность работает;
* заодно нормализуется `delivered` (NULL → 0) и удаляются дубликаты
  `(url, video, user_id)` с сохранением самой свежей записи и флага `delivered`.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

CURRENT_VERSION = 1
BACKUP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

CREATE_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
    date DATETIME,
    user_id INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL,
    video BOOLEAN NOT NULL DEFAULT 0,
    file_name TEXT,
    file_size INTEGER,
    title TEXT,
    artist TEXT,
    channel TEXT,
    delivered BOOLEAN NOT NULL DEFAULT 0
)
"""

CREATE_DOWNLOADS_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_downloads ON downloads(url, video, user_id)"
)

CREATE_SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL,
    keywords TEXT,
    interval INTEGER NOT NULL DEFAULT 3600,
    last_check DATETIME
)
"""

#: Колонки, которых не было в исходном `CREATE TABLE` (D1).
LEGACY_DOWNLOAD_COLUMNS = (("artist", "TEXT"), ("channel", "TEXT"), ("delivered", "BOOLEAN"))

DEDUPLICATE = """
DELETE FROM downloads
WHERE rowid NOT IN (
    SELECT rowid FROM (
        SELECT rowid,
               ROW_NUMBER() OVER (
                   PARTITION BY COALESCE(url, ''), COALESCE(video, 0), COALESCE(user_id, 0)
                   ORDER BY COALESCE(date, '') DESC, rowid DESC
               ) AS rn
        FROM downloads
    ) WHERE rn = 1
)
"""

#: `delivered` в группе дубликатов должен остаться выставленным, если был хоть где-то.
PROPAGATE_DELIVERED = """
UPDATE downloads SET delivered = COALESCE((
    SELECT MAX(COALESCE(other.delivered, 0)) FROM downloads AS other
    WHERE COALESCE(other.url, '') = COALESCE(downloads.url, '')
      AND COALESCE(other.video, 0) = COALESCE(downloads.video, 0)
      AND COALESCE(other.user_id, 0) = COALESCE(downloads.user_id, 0)
), 0)
"""


@dataclass
class MigrationReport:
    """Что произошло при миграции (или что произошло бы при `--check`)."""

    path: Path
    from_version: int
    to_version: int
    applied: bool = False
    backup_path: Path | None = None
    rows_before: int = 0
    rows_after: int = 0
    null_user_ids_fixed: int = 0
    non_numeric_user_ids: int = 0
    duplicates_removed: int = 0
    delivered_normalized: int = 0
    integrity: str = "not checked"
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.from_version != self.to_version

    def as_text(self) -> str:
        lines = [
            f"база: {self.path}",
            f"версия схемы: {self.from_version} -> {self.to_version}"
            + (" (применено)" if self.applied else " (не применялось)"),
            f"записей в downloads: {self.rows_before} -> {self.rows_after}",
        ]
        if self.backup_path:
            lines.append(f"бэкап: {self.backup_path}")
        details = [
            ("user_id NULL -> 0", self.null_user_ids_fixed),
            ("нечисловых user_id осталось", self.non_numeric_user_ids),
            ("удалено дубликатов", self.duplicates_removed),
            ("нормализовано delivered", self.delivered_normalized),
        ]
        lines.extend(f"  {title}: {value}" for title, value in details if value)
        if self.integrity != "not checked":
            lines.append(f"integrity_check: {self.integrity}")
        lines.extend(f"! {note}" for note in self.notes)
        return "\n".join(lines)


async def get_user_version(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _set_user_version(conn: aiosqlite.Connection, version: int) -> None:
    await conn.execute(f"PRAGMA user_version = {int(version)}")


async def table_columns(conn: aiosqlite.Connection, table: str) -> dict[str, str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1]: row[2] for row in await cursor.fetchall()}


async def _table_exists(conn: aiosqlite.Connection, table: str) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    return await cursor.fetchone() is not None


async def _count(conn: aiosqlite.Connection, sql: str) -> int:
    cursor = await conn.execute(sql)
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _ensure_column(
    conn: aiosqlite.Connection, table: str, column: str, column_type: str
) -> bool:
    """Добавляет колонку, если её нет (SQLite не умеет `ADD COLUMN IF NOT EXISTS`)."""
    if column in await table_columns(conn, table):
        return False
    await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    logging.info("миграция: добавлена колонка %s.%s %s", table, column, column_type)
    return True


async def _migrate_to_v1(conn: aiosqlite.Connection, report: MigrationReport) -> None:
    """Приводит схему и данные к версии 1 (идемпотентно)."""
    await conn.execute(CREATE_DOWNLOADS)
    await conn.execute(CREATE_SUBSCRIPTIONS)
    for column, column_type in LEGACY_DOWNLOAD_COLUMNS:
        await _ensure_column(conn, "downloads", column, column_type)

    # Индекс снимается на время нормализации: приведение NULL → 0 может сделать
    # строки дубликатами, а UNIQUE-индекс не даст обновить ни одну из них.
    # Заново индекс создаётся уже после дедупликации.
    await conn.execute("DROP INDEX IF EXISTS idx_downloads")

    report.rows_before = await _count(conn, "SELECT COUNT(*) FROM downloads")

    cursor = await conn.execute("UPDATE downloads SET user_id = 0 WHERE user_id IS NULL")
    report.null_user_ids_fixed = cursor.rowcount or 0

    # Приводить «текстовые» user_id к числу бессмысленно: в SQLite тип хранения
    # определяет affinity колонки, и для TEXT-колонки CAST тут же вернётся в
    # текст. Поэтому такие строки только считаем и сообщаем о них.
    report.non_numeric_user_ids = await _count(
        conn, "SELECT COUNT(*) FROM downloads WHERE typeof(user_id) != 'integer'"
    )
    if report.non_numeric_user_ids:
        report.notes.append(
            f"осталось {report.non_numeric_user_ids} строк с нечисловым user_id — "
            "они не привязаны к пользователю, но и не удалены"
        )

    cursor = await conn.execute(
        "UPDATE downloads SET video = COALESCE(video, 0), delivered = COALESCE(delivered, 0) "
        "WHERE video IS NULL OR delivered IS NULL"
    )
    report.delivered_normalized = cursor.rowcount or 0

    await conn.execute(PROPAGATE_DELIVERED)
    cursor = await conn.execute(DEDUPLICATE)
    report.duplicates_removed = cursor.rowcount or 0
    if report.duplicates_removed:
        logging.warning("миграция: удалено дубликатов записей: %s", report.duplicates_removed)

    # Индекс создаётся после дедупликации: иначе UNIQUE не даст его создать.
    await conn.execute(CREATE_DOWNLOADS_INDEX)

    report.rows_after = await _count(conn, "SELECT COUNT(*) FROM downloads")
    await _set_user_version(conn, 1)
    await conn.commit()


async def migrate(
    conn: aiosqlite.Connection,
    *,
    path: str | Path | None = None,
    backup: bool = True,
    apply: bool = True,
) -> MigrationReport:
    """Доводит схему до `CURRENT_VERSION`.

    `apply=False` — режим проверки: считает, что было бы сделано, но не меняет
    данные (используется командой `--check`).
    """
    db_path = Path(path) if path is not None else None
    from_version = await get_user_version(conn)
    report = MigrationReport(
        path=db_path or Path(":memory:"),
        from_version=from_version,
        to_version=CURRENT_VERSION,
    )

    if from_version > CURRENT_VERSION:
        raise RuntimeError(
            f"версия схемы {from_version} новее, чем понимает код ({CURRENT_VERSION}); "
            "обновите приложение"
        )
    if from_version == CURRENT_VERSION:
        report.rows_before = report.rows_after = await _count(
            conn, "SELECT COUNT(*) FROM downloads"
        ) if await _table_exists(conn, "downloads") else 0
        report.applied = False
        return report

    if not apply:
        report.notes.append("режим --check: изменения не применялись")
        if not await _table_exists(conn, "downloads"):
            report.notes.append("таблицы downloads нет — база пустая, будет создана с нуля")
            return report
        report.rows_before = report.rows_after = await _count(
            conn, "SELECT COUNT(*) FROM downloads"
        )
        report.null_user_ids_fixed = await _count(
            conn, "SELECT COUNT(*) FROM downloads WHERE user_id IS NULL"
        )
        report.duplicates_removed = await _count(
            conn,
            "SELECT COALESCE(SUM(cnt - 1), 0) FROM ("
            "  SELECT COUNT(*) AS cnt FROM downloads"
            "  GROUP BY COALESCE(url, ''), COALESCE(video, 0), COALESCE(user_id, 0)"
            "  HAVING COUNT(*) > 1)",
        )
        report.to_version = CURRENT_VERSION
        return report

    if backup and await _needs_backup(conn, db_path):
        report.backup_path = _make_backup(db_path)

    await _migrate_to_v1(conn, report)
    report.applied = True
    report.integrity = await _integrity_check(conn)
    return report


async def _needs_backup(conn: aiosqlite.Connection, db_path: Path | None) -> bool:
    """Копия нужна только для существующей базы с данными.

    Свежий файл SQLite создаёт сам при `connect()` (и сразу пишет заголовок при
    включении WAL), поэтому «файл есть» — не признак данных. Признак — уже
    существующая таблица `downloads`.
    """
    if db_path is None or not db_path.exists() or db_path.stat().st_size == 0:
        return False
    return await _table_exists(conn, "downloads")


def _make_backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    backup_path = db_path.with_name(f"{db_path.name}.{stamp}.bak")
    shutil.copy2(db_path, backup_path)
    logging.info("миграция: создан бэкап %s", backup_path)
    return backup_path


async def _integrity_check(conn: aiosqlite.Connection) -> str:
    try:
        cursor = await conn.execute("PRAGMA integrity_check")
        rows = await cursor.fetchall()
        return ", ".join(str(row[0]) for row in rows)
    except Exception as exc:  # pragma: no cover - диагностика не должна ломать миграцию
        return f"ошибка проверки: {exc}"


async def report(conn: aiosqlite.Connection, path: str | Path | None = None) -> dict[str, Any]:
    """Сводка по базе для `--report` (только чтение)."""
    version = await get_user_version(conn)
    has_downloads = await _table_exists(conn, "downloads")
    data: dict[str, Any] = {"path": str(path) if path else ":memory:", "user_version": version}

    if has_downloads:
        columns = await table_columns(conn, "downloads")
        data["downloads"] = await _count(conn, "SELECT COUNT(*) FROM downloads")
        data["user_id_null"] = await _count(
            conn, "SELECT COUNT(*) FROM downloads WHERE user_id IS NULL"
        )
        data["user_id_not_integer"] = await _count(
            conn, "SELECT COUNT(*) FROM downloads WHERE typeof(user_id) != 'integer'"
        )
        data["duplicate_groups"] = await _count(
            conn,
            "SELECT COUNT(*) FROM ("
            "  SELECT 1 FROM downloads"
            "  GROUP BY COALESCE(url, ''), COALESCE(video, 0), COALESCE(user_id, 0)"
            "  HAVING COUNT(*) > 1)",
        )
        data["file_size_null"] = await _count(
            conn, "SELECT COUNT(*) FROM downloads WHERE file_size IS NULL"
        )
        if "delivered" in columns:
            data["delivered"] = await _count(conn, "SELECT COUNT(*) FROM downloads WHERE delivered")
        data["columns"] = sorted(columns)

    if await _table_exists(conn, "subscriptions"):
        data["subscriptions"] = await _count(conn, "SELECT COUNT(*) FROM subscriptions")

    return data
