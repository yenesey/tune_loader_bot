#!/usr/bin/python3
"""Точка входа: watchdog подписок и long polling.

Обработчики Telegram живут в `tune_loader_bot.telegram`, медиатека — в
`tune_loader_bot.library`, watchdog — в `tune_loader_bot.jobs`, распознавание
речи — в `tune_loader_bot.transcription`, хранилище — в
`tune_loader_bot.storage`. Здесь осталась только композиция запуска (фаза 8).
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from tune_loader_bot.config import get_settings
from tune_loader_bot.jobs.watchdog import SubscriptionWatcher
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.storage.db import Storage
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext
from tune_loader_bot.telegram.voice import TelegramVoiceTranscriber
from tune_loader_bot.transcription.whisper_service import WhisperTranscriber


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
        transcriber=TelegramVoiceTranscriber(WhisperTranscriber(settings)),
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
