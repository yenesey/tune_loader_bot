"""Тесты middleware доступа и фабрики диспетчера (D19/D25)."""

import pytest
from aiogram.fsm.storage.memory import MemoryStorage
from fakes import FakeUser

from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext
from tune_loader_bot.telegram.middleware import SecurityMiddleware


class HandlerSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, event, data):
        self.calls += 1
        return "handled"


@pytest.fixture
def middleware():
    return SecurityMiddleware([111, 222])


async def test_allows_known_user(middleware):
    spy = HandlerSpy()

    result = await middleware(spy, object(), {"event_from_user": FakeUser(id=111)})

    assert result == "handled"
    assert spy.calls == 1


async def test_rejects_unknown_user(middleware, caplog):
    spy = HandlerSpy()

    with caplog.at_level("WARNING"):
        result = await middleware(spy, object(), {"event_from_user": FakeUser(id=999)})

    assert result is None
    assert spy.calls == 0
    assert "999" in caplog.text


async def test_channel_post_without_user_passes(middleware):
    """В постах канала `event_from_user` не задан (aiogram: user = None)."""
    spy = HandlerSpy()

    assert await middleware(spy, object(), {"event_chat": object()}) == "handled"
    assert spy.calls == 1


async def test_does_not_log_message_contents(middleware, caplog):
    """D25: раньше middleware печатал `data.keys()`."""
    secret = "очень секретный текст сообщения"

    with caplog.at_level("DEBUG"):
        await middleware(HandlerSpy(), object(), {"event_from_user": FakeUser(id=999), "text": secret})

    assert secret not in caplog.text


def test_allowed_users_are_normalised():
    middleware = SecurityMiddleware(["111", 222])

    assert middleware.allowed == frozenset({111, 222})


def test_create_dispatcher_registers_middleware_and_router(settings, storage):
    """D19: middleware и обработчики собираются в одном месте (в бою и в тестах)."""
    library = MediaLibrary(settings, storage)
    ctx = HandlerContext(
        settings=settings, library=library, delivery=Delivery(settings, library)
    )

    dispatcher = create_dispatcher(ctx)

    assert isinstance(dispatcher.fsm.storage, MemoryStorage)

    # aiogram регистрирует на `update` свои внешние middleware (контекст
    # пользователя и т.п.), поэтому проверяем именно наличие нашего.
    outer = list(dispatcher.update.outer_middleware)
    ours = [item for item in outer if isinstance(item, SecurityMiddleware)]
    assert len(ours) == 1, f"наш middleware должен быть ровно один, всего: {outer}"
    assert ours[0].allowed == frozenset(settings.users_list)
    assert len(dispatcher.sub_routers) == 1
