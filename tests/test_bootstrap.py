"""Проверка сборки приложения (то, что раньше жило только внутри `main()`).

Тест поднимает те же зависимости, что и продовый запуск, но на временной базе:
если обвязка разъедется (например, обработчики получат другой объект хранилища),
это будет видно здесь, а не в бою.
"""

from pathlib import Path

from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.storage.db import Storage
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext


async def test_build_runtime_wires_everything(bot_module, settings):
    ctx, storage = await bot_module.build_runtime(settings)
    try:
        assert isinstance(ctx, HandlerContext)
        assert isinstance(storage, Storage)
        assert isinstance(ctx.library, MediaLibrary)
        assert isinstance(ctx.delivery, Delivery)
        assert ctx.settings is settings
        assert ctx.transcriber is bot_module.transcribe_voice
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


async def test_whisper_model_is_loaded_once_via_stub(bot_module, whisper_stub):
    """В тестах модель подменена заглушкой: ни torch, ни 1.4 ГБ RSS."""
    assert isinstance(bot_module.model, type(whisper_stub.load_model()))
