"""Устаревший dict-API хранилища (переходный слой).

`bot.py` и watchdog пока работают с dict'ами и с классом `Database`. Этот модуль
транслирует старые вызовы в `tune_loader_bot.storage.db.Storage` и исчезнет
после переписывания обработчиков и watchdog'а (фазы 5-6).

Отличия от прежнего поведения (это исправления D1/D2/D7/D8/D15):

* путь к базе берётся из настроек (`db-path`), а не из жёсткого `downloads.db`;
* соединение принадлежит экземпляру, а не классу-синглтону, и его можно закрыть;
* `find_url` без `user_id` возвращает самую свежую запись, а не произвольную;
* `save_download` при конфликте обновляет все поля, а не только `delivered`.
"""

from __future__ import annotations

from typing import Any, Mapping

from tune_loader_bot.config import get_settings
from tune_loader_bot.storage.db import Storage


class Database:
    """Совместимая обёртка над `Storage` (dict-и на входе и выходе)."""

    def __init__(self) -> None:
        self._storage: Storage | None = None

    @classmethod
    async def create(cls) -> "Database":
        instance = cls()
        instance._storage = await Storage.open(get_settings().db_path)
        return instance

    @property
    def _conn(self):  # noqa: ANN201 - совместимость со старым кодом/тестами
        return self._require_storage().connection

    def _require_storage(self) -> Storage:
        if self._storage is None:
            raise RuntimeError("Database не инициализирована: вызовите await Database.create()")
        return self._storage

    async def close(self) -> None:
        await self._require_storage().close()

    async def save_download(self, download: Mapping[str, Any]) -> None:
        await self._require_storage().save_download(download)

    async def find_url(
        self, url: str, video: bool = False, user_id: int | str | None = None
    ) -> dict[str, Any] | None:
        found = await self._require_storage().find_download(url, video, user_id)
        return found.to_legacy_dict() if found else None

    async def get_subscriptions(self) -> list[dict[str, Any]]:
        return [sub.to_legacy_dict() for sub in await self._require_storage().subscriptions()]

    async def save_subscription(self, subscription: Mapping[str, Any]) -> None:
        await self._require_storage().save_subscription(subscription)
