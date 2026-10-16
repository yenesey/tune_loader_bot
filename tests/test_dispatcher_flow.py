"""Сквозной тест через `Dispatcher.feed_update`.

Прямые вызовы обработчиков (см. `test_handlers.py`) не проверяют регистрацию
фильтров и передачу состояния между апдейтами. Здесь апдейты идут через
настоящий aiogram-диспетчер, а вместо сети — сессия-заглушка.
"""

from datetime import datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageMedia,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from fakes import make_downloader

from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.telegram import texts
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.dispatcher import create_dispatcher
from tune_loader_bot.telegram.handlers import HandlerContext

URL = "https://youtu.be/dQw4w9WgXcQ"
CHAT_ID = 111
STRANGER_ID = 999
USER = User(id=CHAT_ID, is_bot=False, first_name="Tester")
STRANGER = User(id=STRANGER_ID, is_bot=False, first_name="Stranger")
CHAT = Chat(id=CHAT_ID, type="private")
CHANNEL = Chat(id=-1001234567890, type="channel")


class StubSession(BaseSession):
    """Сессия без сети: записывает вызовы API и отдаёт правдоподобные ответы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        yield b""

    async def make_request(
        self, bot: Bot, method: TelegramMethod, timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage):
            # как в aiogram: ответ монтируется к боту, иначе `edit_media` на нём
            # нельзя вызвать
            return Message(
                message_id=len(self.calls),
                date=datetime.now(),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            ).as_(bot)
        if isinstance(method, (EditMessageMedia, EditMessageText)):
            return Message(message_id=1, date=datetime.now(), chat=CHAT).as_(bot)
        if isinstance(method, DeleteMessage):
            return True
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    def of(self, kind: type) -> list[Any]:
        return [call for call in self.calls if isinstance(call, kind)]


@pytest.fixture
async def flow(settings, storage):
    session = StubSession()
    bot = Bot(token="123456789:TEST-TOKEN", session=session)
    library = MediaLibrary(settings, storage, downloader=make_downloader())
    ctx = HandlerContext(
        settings=settings, library=library, delivery=Delivery(settings, library)
    )
    dispatcher = create_dispatcher(ctx)
    yield dispatcher, bot, session
    await bot.session.close()


def private_message(update_id: int, message_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=message_id, date=datetime.now(), chat=CHAT, from_user=USER, text=text
        ),
    )


def button_press(update_id: int, data: str) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"callback-{update_id}",
            from_user=USER,
            chat_instance="chat-instance",
            data=data,
            message=Message(
                message_id=20,
                date=datetime.now(),
                chat=CHAT,
                text=texts.DIALOG_QUESTION,
            ),
        ),
    )


async def test_link_then_audio_button(flow, storage):
    """Ссылка → диалог → кнопка Audio → файл уходит правкой сообщения."""
    dispatcher, bot, session = flow

    await dispatcher.feed_update(bot, private_message(1, 10, URL))

    questions = session.of(SendMessage)
    assert questions and questions[0].text == texts.DIALOG_QUESTION
    assert questions[0].reply_markup is not None

    # FSM-состояние сохранено диспетчером, поэтому кнопка знает ссылку
    await dispatcher.feed_update(bot, button_press(2, "audio"))

    assert session.of(EditMessageMedia), "файл должен уйти через edit_media"
    assert not session.of(EditMessageText)
    assert any(call.text == texts.PROCESSING for call in session.of(SendMessage))
    assert await storage.find_download(URL, False, CHAT_ID) is not None


async def test_stranger_is_blocked_by_middleware(flow):
    dispatcher, bot, session = flow

    await dispatcher.feed_update(
        bot,
        Update(
            update_id=5,
            message=Message(
                message_id=11,
                date=datetime.now(),
                chat=Chat(id=STRANGER_ID, type="private"),
                from_user=STRANGER,
                text=URL,
            ),
        ),
    )

    assert session.calls == [], "апдейт чужого пользователя не должен обрабатываться"


async def test_channel_post_is_downloaded_without_dialog(flow, storage):
    dispatcher, bot, session = flow

    await dispatcher.feed_update(
        bot,
        Update(
            update_id=6,
            channel_post=Message(
                message_id=12, date=datetime.now(), chat=CHANNEL, text=URL
            ),
        ),
    )

    assert not [call for call in session.of(SendMessage) if call.text == texts.DIALOG_QUESTION]
    assert session.of(EditMessageMedia)
    saved = await storage.find_download(URL, False, 0)
    assert saved is not None and saved.user_id == 0
