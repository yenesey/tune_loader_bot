"""Проверка сборки приложения (то, что раньше жило только внутри `main()`).

Тест поднимает те же зависимости, что и продовый запуск, но на временной базе:
если обвязка разъедется (например, обработчики получат другой объект хранилища),
это будет видно здесь, а не в бою.
"""

from datetime import datetime
from pathlib import Path

from fakes import FakeBot

from tune_loader_bot.domain.models import Subscription
from tune_loader_bot.jobs.watchdog import SubscriptionWatcher
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.storage.db import Storage
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext
from tune_loader_bot.telegram.voice import TelegramVoiceTranscriber


async def test_build_runtime_wires_everything(bot_module, settings):
    ctx, storage = await bot_module.build_runtime(settings)
    try:
        assert isinstance(ctx, HandlerContext)
        assert isinstance(storage, Storage)
        assert isinstance(ctx.library, MediaLibrary)
        assert isinstance(ctx.delivery, Delivery)
        assert ctx.settings is settings
        assert isinstance(ctx.transcriber, TelegramVoiceTranscriber)
        assert storage.path == Path("downloads.db")
        assert Path("downloads.db").exists()
    finally:
        await storage.close()


async def test_runtime_dispatcher_uses_same_context(bot_module, settings):
    ctx, storage = await bot_module.build_runtime(settings)
    try:
        dispatcher = create_dispatcher(ctx)
        assert ctx.transcriber is not None
        assert len(dispatcher.sub_routers) == 1
    finally:
        await storage.close()


async def test_watcher_is_built_from_context(bot_module, settings):
    """Watchdog собирается из тех же зависимостей, что и обработчики (как в `main`)."""
    ctx, storage = await bot_module.build_runtime(settings)
    try:
        watcher = SubscriptionWatcher(
            bot=FakeBot(),
            library=ctx.library,
            delivery=ctx.delivery,
            settings=settings,
        )
        assert watcher.is_due(Subscription(url="u"), datetime.now()) is True
    finally:
        await storage.close()


def test_importing_bot_does_not_load_whisper(project, monkeypatch):
    """D14: раньше `import bot` тянул torch и ~1.4 ГБ RSS."""
    import importlib
    import sys
    import types

    sentinel = types.ModuleType("whisper")
    loaded: list = []
    sentinel.load_model = lambda *args, **kwargs: loaded.append(args) or types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "whisper", sentinel)
    sys.modules.pop("bot", None)
    try:
        importlib.import_module("bot")
        assert loaded == [], "модель не должна грузиться на импорте"
    finally:
        sys.modules.pop("bot", None)


async def test_transcriber_is_lazy_until_first_voice(bot_module, settings):
    """Модель не грузится ни при импорте, ни при сборке runtime (D14)."""
    ctx, storage = await bot_module.build_runtime(settings)
    try:
        assert isinstance(ctx.transcriber, TelegramVoiceTranscriber)
        assert ctx.transcriber.enabled is True
        assert ctx.transcriber.model_loaded is False
    finally:
        await storage.close()
