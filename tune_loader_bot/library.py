"""Прикладной слой: «медиатека» — найти готовое или скачать и запомнить.

Раньше это было размазано по `bot.py` (`find`, `download`, `process_message`),
причём:

* `find_url` не проверял, что файл вообще есть на диске (D7) — запись могла
  ссылаться на удалённый файл, и отправка падала;
* `download` собирал словарь руками, а `user_id` приезжал то строкой, то числом;
* пользователь и watchdog ходили в БД напрямую.

Теперь это один объект с явными зависимостями: его используют и обработчики
(фаза 5), и watchdog (фаза 6). Зависимость `downloader` инъектируется, поэтому
тесты работают без сети.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from tune_loader_bot.config import Settings
from tune_loader_bot.domain import services
from tune_loader_bot.media import paths, ytdlp
from tune_loader_bot.storage.db import Storage

LOGGER = logging.getLogger(__name__)

#: Функция скачивания: (каталог, сервис, url, video) → запись о файле или None.
Downloader = Callable[[Any, "str | None", str, bool], Awaitable[dict[str, Any] | None]]


class MediaLibrary:
    """Поиск уже скачанного и загрузка нового."""

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        *,
        downloader: Downloader | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._downloader = downloader or self._download_via_ytdlp
        self._now = now

    @property
    def storage(self) -> Storage:
        return self._storage

    @property
    def settings(self) -> Settings:
        return self._settings

    async def find(
        self, url: str, video: bool = False, user_id: int | None = None
    ) -> dict[str, Any] | None:
        """Готовая запись из БД — но только если файл на месте (D7)."""
        found = await self._storage.find_download(url, video, user_id)
        if found is None:
            return None

        record = self._enrich(found.to_legacy_dict())
        if record is None:
            return None
        if not paths.exists(record):
            LOGGER.warning(
                "запись есть, а файла нет на диске — качаем заново: %s", record.get("file_path")
            )
            return None
        return record

    async def download(
        self, url: str, video: bool = False, user_id: int | None = None
    ) -> dict[str, Any] | None:
        """Скачивает файл, сохраняет запись в БД и возвращает её с ссылками."""
        timestamp = self._now()
        target = paths.target_dir(self._settings.download_dir, timestamp)
        target.mkdir(parents=True, exist_ok=True)

        service = services.match(url)
        loaded = await self._downloader(
            target, service.name if service else None, url, video
        )
        if not loaded:
            LOGGER.error("не удалось скачать %s", url)
            return None

        loaded.update(
            {
                "date": timestamp,
                "user_id": user_id if user_id is not None else 0,
                "url": url,
                "video": int(video),
                "delivered": int(False),
            }
        )
        await self._storage.save_download(loaded)
        return self._enrich(loaded)

    async def get_or_download(
        self, url: str, video: bool = False, user_id: int | None = None
    ) -> dict[str, Any] | None:
        found = await self.find(url, video, user_id)
        if found is not None:
            return found
        return await self.download(url, video, user_id)

    async def mark_delivered(self, record: dict[str, Any]) -> None:
        record["delivered"] = int(True)
        await self._storage.save_download(record)

    # --- внутреннее -------------------------------------------------------

    async def _download_via_ytdlp(
        self, target: Any, service: str | None, url: str, video: bool
    ) -> dict[str, Any] | None:
        result = await ytdlp.download_media(self._settings, target, service, url, video=video)
        return result.as_dict() if result else None

    def _enrich(self, record: dict[str, Any]) -> dict[str, Any] | None:
        return paths.enrich_info(
            record,
            download_dir=self._settings.download_dir,
            server_root_url=self._settings.server_root_url,
        )
