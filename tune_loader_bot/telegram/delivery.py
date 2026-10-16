"""Отправка готового файла в чат.

Два сценария сохранены как были:

* **личный диалог** (`send_inline`) — бот правит своё сообщение «Processing…»
  через `edit_media`/`edit_text`, поэтому в чате остаётся одно сообщение;
* **watchdog** (`send_to_chat`) — бот отправляет новое аудио/сообщение и
  помечает запись доставленной.

Общая часть (размер, подписи, выбор audio/video, отметка `delivered`) — здесь,
раньше она дублировалась в `process_message` и `send_audio_message` (D21).

Проверка размера больше не может упасть на `file_size = None` (D6): размер
берётся из записи, а если его нет — с диска.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaAudio, InputMediaVideo, Message

from tune_loader_bot.config import Settings
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.telegram import captions

LOGGER = logging.getLogger(__name__)


class Delivery:
    """Доставка файлов и ссылок в Telegram."""

    def __init__(self, settings: Settings, library: MediaLibrary) -> None:
        self._settings = settings
        self._library = library

    def size_of(self, record: Mapping[str, Any]) -> int | None:
        """Размер файла: из записи, иначе с диска (может быть `None`)."""
        size = record.get("file_size")
        if isinstance(size, int) and size >= 0:
            return size
        path = record.get("file_path")
        if path and Path(str(path)).is_file():
            return Path(str(path)).stat().st_size
        return None

    def fits_telegram(self, record: Mapping[str, Any]) -> bool:
        size = self.size_of(record)
        return size is not None and size < self._settings.tg_max_file_size

    def media(self, record: Mapping[str, Any]) -> InputMediaAudio | InputMediaVideo:
        """Медиа-объект для `edit_media`."""
        media_class = InputMediaVideo if record.get("video") else InputMediaAudio
        return media_class(
            media=FSInputFile(record["file_path"]),
            title=record.get("title"),
            performer=record.get("performer"),
            caption=captions.media_caption(record),
        )

    async def send_inline(self, placeholder: Message, record: Mapping[str, Any]) -> bool:
        """Правит сообщение-заглушку. `False` — файла нет, отправить нечего."""
        if self.size_of(record) is None:
            LOGGER.error("нечего отправлять: нет файла %s", record.get("file_path"))
            return False

        if self.fits_telegram(record):
            await placeholder.edit_media(self.media(record))
        else:
            await placeholder.edit_text(captions.link_message(record))
        return True

    async def send_to_chat(self, bot: Bot, chat_id: int, record: Mapping[str, Any]) -> bool:
        """Новое сообщение в чат (watchdog). Помечает запись доставленной."""
        if self.size_of(record) is None:
            LOGGER.error("нечего отправлять: нет файла %s", record.get("file_path"))
            return False

        if self.fits_telegram(record):
            await bot.send_audio(
                chat_id,
                FSInputFile(record["file_path"]),
                title=record.get("title"),
                performer=record.get("performer"),
                caption=captions.media_caption(record),
            )
        else:
            await bot.send_message(chat_id, captions.link_message(record))

        await self._library.mark_delivered(dict(record))
        return True
