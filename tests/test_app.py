"""Тесты композиции приложения и логирования (фаза 8, D19/D25)."""

import importlib
import logging

import pytest
from aiogram.fsm.storage.memory import MemoryStorage

from tune_loader_bot import app as app_module
from tune_loader_bot import logging_setup
from tune_loader_bot.telegram.middleware import SecurityMiddleware


@pytest.fixture
async def application(settings):
    application = await app_module.build_app(settings)
    yield application
    if not application.closed:
        await application.shutdown()


async def test_build_app_wires_everything(application, settings):
    assert application.settings is settings
    assert isinstance(application.dispatcher.fsm.storage, MemoryStorage)

    outer = [
        item
        for item in application.dispatcher.update.outer_middleware
        if isinstance(item, SecurityMiddleware)
    ]
    assert len(outer) == 1
    assert outer[0].allowed == frozenset(settings.users_list)

    assert application.ctx.library is not None
    assert application.ctx.delivery is not None
    assert application.ctx.transcriber is not None
    assert application.ctx.transcriber.model_loaded is False, "модель грузится лениво (D14)"
    assert application.watcher_started is False
    assert application.closed is False


async def test_start_then_shutdown_releases_resources(application):
    """D19: остановка отменяет watchdog и закрывает БД и сессию бота."""
    await application.start()
    assert application.watcher_started is True

    await application.shutdown()

    assert application.closed is True
    assert application.watcher_started is False
    with pytest.raises(ValueError, match="no active connection"):
        await application.storage.count_downloads()


async def test_shutdown_is_idempotent(application):
    await application.shutdown()
    await application.shutdown()

    assert application.closed is True


async def test_shutdown_without_start(application):
    await application.shutdown()

    assert application.closed is True
    assert application.watcher_started is False


async def test_run_builds_starts_and_stops(settings, monkeypatch):
    polled: list[str] = []

    async def fake_poll(self):
        polled.append("poll")

    monkeypatch.setattr(app_module.Application, "poll", fake_poll)

    await app_module.run(settings)

    assert polled == ["poll"]


def test_main_reports_missing_settings(project):
    """D17: без settings.json — понятное сообщение и код возврата 2."""
    (project / "settings.json").unlink()
    importlib.import_module("tune_loader_bot.config").reset_settings_cache()

    with pytest.raises(SystemExit) as exc:
        app_module.main()

    assert exc.value.code == 2


def test_importing_bot_does_not_load_whisper(project, monkeypatch):
    """D14: раньше `import bot` тянул torch и ~4.5 ГБ RSS на импорте."""
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


def test_module_entry_point_exists():
    module = importlib.import_module("tune_loader_bot.__main__")

    assert module.main is app_module.main


def test_bot_py_is_a_shim_for_the_app():
    bot_module = importlib.import_module("bot")

    assert bot_module.main is app_module.main


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("DEBUG", logging.DEBUG),
        ("info", logging.INFO),
        (" Warning ", logging.WARNING),
        (None, logging.INFO),
        ("", logging.INFO),
        (25, 25),
    ],
)
def test_resolve_level(value, expected):
    assert logging_setup.resolve_level(value) == expected


def test_unknown_level_falls_back_with_warning(caplog):
    with caplog.at_level("WARNING"):
        assert logging_setup.resolve_level("очень подробно") == logging.INFO

    assert "неизвестный уровень" in caplog.text


@pytest.mark.parametrize(
    ("level", "expected"),
    [("DEBUG", logging.DEBUG), ("WARNING", logging.WARNING)],
)
def test_setup_logging_uses_settings_level(settings, level, expected):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        logging_setup.setup_logging(settings.model_copy(update={"log_level": level}), force=True)

        assert root.level == expected
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_setup_logging_without_settings(settings):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        logging_setup.setup_logging(force=True)

        assert root.level == logging.INFO
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
