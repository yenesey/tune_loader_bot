"""Композиция приложения: сборка зависимостей и жизненный цикл.

Раньше всё это лежало в `bot.py` (`main`) вперемешку с обработчиками и циклом
watchdog'а. Теперь точка входа — тонкий шим (`bot.py` → `python -m
tune_loader_bot`), а сборка и остановка живут здесь и проверяются тестами.

D19: появилась аккуратная остановка — watchdog отменяется, соединение с БД и
сессия бота закрываются; ошибки запуска не оставляют висящих задач.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from tune_loader_bot.config import ConfigError, Settings, get_settings
from tune_loader_bot.jobs.watchdog import SubscriptionWatcher
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.logging_setup import setup_logging
from tune_loader_bot.storage.db import Storage
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext
from tune_loader_bot.telegram.voice import TelegramVoiceTranscriber
from tune_loader_bot.transcription.whisper_service import WhisperTranscriber

LOGGER = logging.getLogger(__name__)


@dataclass
class Application:
    """Собранное приложение: контекст, хранилище, диспетчер, бот и watchdog."""

    settings: Settings
    ctx: HandlerContext
    storage: Storage
    dispatcher: Dispatcher
    bot: Bot
    watcher: SubscriptionWatcher
    watcher_started: bool = False
    closed: bool = False

    async def start(self) -> None:
        self.watcher.start()
        self.watcher_started = True
        subscriptions = await self.storage.subscriptions()
        LOGGER.info("бот запущен: подписок в базе — %s", len(subscriptions))

    async def shutdown(self) -> None:
        """Останавливает фоновые задачи и закрывает ресурсы (идемпотентно)."""
        if self.closed:
            return
        self.closed = True
        if self.watcher_started:
            await self.watcher.stop()
            self.watcher_started = False
        await self.storage.close()
        await self.bot.session.close()
        LOGGER.info("бот остановлен")

    async def poll(self) -> None:
        """Long polling (возвращается после сигнала остановки)."""
        await self.dispatcher.start_polling(self.bot)


async def build_app(settings: Settings) -> Application:
    """Собирает приложение из настроек (без обращения к Telegram)."""
    storage = await Storage.open(settings.db_path)
    library = MediaLibrary(settings, storage)
    ctx = HandlerContext(
        settings=settings,
        library=library,
        delivery=Delivery(settings, library),
        transcriber=TelegramVoiceTranscriber(WhisperTranscriber(settings)),
    )
    bot = Bot(
        token=settings.telegram_api_token, default=DefaultBotProperties(parse_mode="HTML")
    )
    return Application(
        settings=settings,
        ctx=ctx,
        storage=storage,
        dispatcher=create_dispatcher(ctx),
        bot=bot,
        watcher=SubscriptionWatcher(
            bot=bot, library=library, delivery=ctx.delivery, settings=settings
        ),
    )


async def run(settings: Settings | None = None) -> None:
    """Полный жизненный цикл: собрать, запустить, дождаться сигнала, остановить."""
    settings = settings or get_settings()
    app = await build_app(settings)
    await app.start()
    try:
        await app.poll()
    finally:
        await app.shutdown()


def main() -> None:
    """Точка входа `python -m tune_loader_bot` (и `python bot.py`)."""
    try:
        settings = get_settings()
    except ConfigError as exc:
        logging.basicConfig(level=logging.ERROR)
        LOGGER.error("%s", exc)
        raise SystemExit(2) from exc

    setup_logging(settings)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:  # pragma: no cover - ручное прерывание
        LOGGER.info("остановлено пользователем")
