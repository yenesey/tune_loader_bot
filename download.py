"""Устаревший фасад скачивания (переходный слой).

`bot.py` вызывает `download_media()` и `download_playlist()` — имена и
сигнатуры сохранены, но внутри работает `tune_loader_bot.media.ytdlp`
(исправления D3/D4/D6/D22/D23/D24). Модуль исчезнет после переписывания
обработчиков и watchdog'а (фазы 5-6).
"""

from __future__ import annotations

from typing import Any

from tune_loader_bot.config import get_settings
from tune_loader_bot.media import ytdlp


async def download_media(
    target_dir: str, service: str | None, url: str, video: bool = False
) -> dict[str, Any] | None:
    """Скачивает один файл. `None` — если скачать не удалось."""
    settings = get_settings()
    result = await ytdlp.download_media(settings, target_dir, service, url, video=video)
    return result.as_dict() if result else None


async def download_playlist(url: str, items: str = "1") -> list[dict[str, Any]]:
    """Разбирает плейлист/канал без скачивания. Ошибки → пустой список."""
    return await ytdlp.download_playlist(get_settings(), url, items=items)
