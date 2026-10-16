"""Распознавание речи через Whisper с ленивой загрузкой модели.

Что исправлено (D14):

* `whisper.load_model("turbo")` вызывался **на импорте** `bot.py`: старт занимал
  десятки секунд и ~1.4 ГБ RSS, даже если голосовые никто не присылает. Теперь
  модель грузится при первом голосовом (в отдельном потоке) и один раз;
* тяжёлый импорт (`whisper` тянет torch) стал ленивым: если пакета нет, фича
  просто выключается с внятным сообщением в лог, а бот работает;
* не было ограничения на параллельные распознавания — теперь `Semaphore(1)`
  (модель не потокобезопасна, а каждое распознавание ест память);
* язык и модель берутся из настроек (`whisper-language`, `whisper-model`),
  появился выключатель `whisper-enabled`.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tune_loader_bot.config import Settings

LOGGER = logging.getLogger(__name__)


class WhisperTranscriber:
    """Файл с речью → текст. Модель загружается при первом обращении."""

    def __init__(
        self, settings: Settings, *, importer: Callable[[], Any] | None = None
    ) -> None:
        self._settings = settings
        self._importer = importer or (lambda: importlib.import_module("whisper"))
        self._model: Any = None
        self._unavailable = False
        self._model_lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(1)

    @property
    def enabled(self) -> bool:
        """Доступно ли распознавание (настройка + наличие whisper)."""
        return self._settings.whisper_enabled and not self._unavailable

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    async def transcribe(self, path: str | Path) -> str | None:
        """Возвращает текст или `None` (нечего распознавать / фича выключена)."""
        if not self.enabled:
            LOGGER.debug("распознавание выключено — файл %s пропущен", path)
            return None

        async with self._semaphore:
            try:
                model = await asyncio.to_thread(self._ensure_model)
            except Exception:
                self._unavailable = True
                LOGGER.exception(
                    "Whisper недоступен — распознавание отключено до перезапуска "
                    "(установите зависимости из requirements-whisper.txt)"
                )
                return None

            result = await asyncio.to_thread(
                model.transcribe, str(path), language=self._settings.whisper_language
            )

        text = ((result or {}).get("text") or "").strip()
        if not text:
            LOGGER.info("речь не распознана: %s", path)
            return None
        return text

    def _ensure_model(self) -> Any:
        """Загрузка модели (синхронная, вызывается в отдельном потоке)."""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    module = self._importer()
                    LOGGER.info(
                        "загрузка модели Whisper %r (один раз)",
                        self._settings.whisper_model,
                    )
                    self._model = module.load_model(self._settings.whisper_model)
        return self._model
