"""Мост между Telegram-голосовым и сервисом распознавания.

Скачивает файл во временный каталог (`TemporaryDirectory` убирает его даже при
исключении — раньше был `NamedTemporaryFile(delete=False)` с ручной уборкой) и
отдаёт в `WhisperTranscriber`. Реализует протокол `VoiceTranscriber`, который
ждут обработчики: `await transcriber(message) -> str | None`.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from aiogram.types import Message

from tune_loader_bot.transcription.whisper_service import WhisperTranscriber

LOGGER = logging.getLogger(__name__)


class TelegramVoiceTranscriber:
    """Голосовое сообщение → текст."""

    def __init__(self, transcriber: WhisperTranscriber) -> None:
        self._transcriber = transcriber

    @property
    def enabled(self) -> bool:
        return self._transcriber.enabled

    @property
    def model_loaded(self) -> bool:
        """Загружена ли модель Whisper (D14: должна грузиться лениво)."""
        return self._transcriber.model_loaded

    async def __call__(self, message: Message) -> str | None:
        if message.voice is None:
            return None

        file = await message.bot.get_file(message.voice.file_id)
        with tempfile.TemporaryDirectory(prefix="tlb-voice-") as directory:
            path = Path(directory) / "voice.ogg"
            await message.bot.download_file(file.file_path, path)
            LOGGER.debug("голосовое сохранено во временный файл %s", path)
            return await self._transcriber.transcribe(path)
