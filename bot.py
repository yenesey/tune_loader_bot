#!/usr/bin/python3
"""Точка входа: модель Whisper, watchdog подписок и long polling.

Обработчики Telegram живут в `tune_loader_bot.telegram`, медиатека — в
`tune_loader_bot.library`, хранилище — в `tune_loader_bot.storage`. Здесь
осталось только то, что ещё не разобрано по фазам: загрузка модели Whisper
(фаза 7), watchdog (фаза 6) и композиция запуска (фаза 8).
"""

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timedelta

import whisper
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from download import download_playlist
from tune_loader_bot.config import get_settings
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.storage.db import Storage
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext

LOGGER = logging.getLogger(__name__)

WATCHDOG_TICK_SECONDS = 60

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


def scan_playlist(keywords: str | None, playlist) -> list[str]:
    """Отбирает ссылки из плейлиста по ключевым словам (фаза 6 перепишет)."""
    urls = []
    for entry in playlist:
        if keywords:
            for keyword in keywords.split(","):
                if keyword in entry["title"]:
                    urls.append(entry["url"])
        else:
            urls.append(entry["url"])
    urls.reverse()  # elders first
    return urls


async def watchdog(bot: Bot, ctx: HandlerContext) -> None:
    """Следит за подписками (фаза 6 вынесет это в `jobs/watchdog.py`)."""
    while True:
        await asyncio.sleep(WATCHDOG_TICK_SECONDS)
        try:
            for sub in await ctx.library.storage.subscriptions():
                if (
                    sub.last_check is not None
                    and sub.last_check + timedelta(seconds=sub.interval) > datetime.now()
                ):
                    continue

                # D13: время проверки сохраняется до самой проверки — фаза 6.
                sub.last_check = datetime.now()
                await ctx.library.storage.save_subscription(sub)

                playlist = await download_playlist(sub.url, "1,2")
                for url in scan_playlist(sub.keywords, playlist):
                    record = await ctx.library.find(url, False, sub.user_id)
                    if record is not None:
                        if not record.get("delivered"):
                            LOGGER.info("запись %s не доставлена — повторная отправка", url)
                            await ctx.delivery.send_to_chat(bot, sub.user_id, record)
                        continue

                    record = await ctx.library.download(url, False, sub.user_id)
                    if record is not None:
                        await ctx.delivery.send_to_chat(bot, sub.user_id, record)
        except Exception:
            LOGGER.exception("ошибка в watchdog")


async def build_runtime(settings):
    """Собирает зависимости приложения (используют `main` и тесты).

    Возвращает контекст обработчиков и хранилище: само хранилище нужно `main`,
    чтобы закрыть соединение при остановке.
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

    watcher = asyncio.create_task(watchdog(bot, ctx))
    try:
        await dispatcher.start_polling(bot)
    finally:
        watcher.cancel()
        await storage.close()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(main())
