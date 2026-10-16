#!/usr/bin/python3
"""Точка входа: модель Whisper, watchdog подписок и long polling.

Обработчики Telegram живут в `tune_loader_bot.telegram`, медиатека — в
`tune_loader_bot.library`, watchdog — в `tune_loader_bot.jobs`, хранилище — в
`tune_loader_bot.storage`. Здесь остались только модель Whisper (фаза 7) и
композиция запуска (фаза 8).
"""

import asyncio
import logging
import os
import tempfile

import whisper
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from tune_loader_bot.config import get_settings
from tune_loader_bot.jobs.watchdog import SubscriptionWatcher
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.storage.db import Storage
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext

# D14: модель грузится на импорте модуля (~1.4 ГБ RSS) — чинится в фазе 7.
model = whisper.load_model("turbo")


async def transcribe_voice(message) -> str | None:
    """Временный адаптер Whisper (в фазе 7 переедет в `transcription/`)."""
    bot = message.bot
    file = await bot.get_file(message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        file_path = tmp.name
    try:
        await bot.download_file(file.file_path, file_path)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: model.transcribe(file_path, language="ru")
        )
        return (result.get("text") or "").strip() or None
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


async def build_runtime(settings):
    """Собирает зависимости приложения (используют `main` и тесты).

    Возвращает контекст обработчиков и хранилище: хранилище нужно `main`, чтобы
    закрыть соединение при остановке.
    """
    storage = await Storage.open(settings.db_path)
    library = MediaLibrary(settings, storage)
    ctx = HandlerContext(
        settings=settings,
        library=library,
        delivery=Delivery(settings, library),
        transcriber=transcribe_voice,
    )
    return ctx, storage


async def main() -> None:
    settings = get_settings()
    ctx, storage = await build_runtime(settings)
    dispatcher = create_dispatcher(ctx)
    bot = Bot(
        token=settings.telegram_api_token, default=DefaultBotProperties(parse_mode="HTML")
    )

    watcher = SubscriptionWatcher(
        bot=bot, library=ctx.library, delivery=ctx.delivery, settings=settings
    )
    watcher.start()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await watcher.stop()
        await storage.close()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(main())
