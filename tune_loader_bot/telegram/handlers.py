"""Обработчики сообщений, callback'ов и постов канала.

Ключевые изменения относительно `bot.py`:

* состояние диалога живёт в FSM (`state.update_data`), а не в глобальном
  `USER_DATA` (D9): рестарт больше не приводит к `KeyError`, а несколько чатов
  одного пользователя не перетирают друг друга; потерянное состояние даёт
  вежливое «ссылка устарела»;
* зависимости (медиатека, доставка, транскрипция) приходят в `HandlerContext`,
  поэтому обработчики тестируются без сети и без Telegram;
* фабрики `make_*_handler` возвращают корутины — их можно вызывать напрямую в
  тестах, а не только через `Dispatcher.feed_update`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from tune_loader_bot.config import Settings
from tune_loader_bot.domain import services
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.telegram import keyboards, texts
from tune_loader_bot.telegram.delivery import Delivery

LOGGER = logging.getLogger(__name__)


class DownloadDialog(StatesGroup):
    """Выбор формата после присланной ссылки."""

    select_action = State()


class VoiceTranscriber(Protocol):
    """Распознавание голосового: текст или `None`, если речи не разобрать."""

    async def __call__(self, message: Message) -> str | None: ...


@dataclass(slots=True)
class HandlerContext:
    """Зависимости обработчиков."""

    settings: Settings
    library: MediaLibrary
    delivery: Delivery
    transcriber: VoiceTranscriber | None = None


async def _try_delete(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # Раньше ошибка удаления превращалась в «Something went wrong…» уже
        # после успешной отправки файла — теперь это только предупреждение.
        LOGGER.warning("не удалось удалить сообщение %s в чате %s", message_id, chat_id)


async def run_download(
    bot: Bot,
    ctx: HandlerContext,
    *,
    chat_id: int,
    url: str,
    video: bool,
    user_id: int | None,
    delete_message_id: int | None = None,
) -> bool:
    """Скачивает (или берёт готовое) и отправляет в чат. `False` — не получилось."""
    try:
        placeholder = await bot.send_message(chat_id, texts.PROCESSING)
        record = await ctx.library.get_or_download(url, video, user_id)
        if record is None or not await ctx.delivery.send_inline(placeholder, record):
            await bot.send_message(chat_id, texts.SOMETHING_WENT_WRONG)
            return False
    except Exception:
        LOGGER.exception("не удалось обработать ссылку %s", url)
        await bot.send_message(chat_id, texts.SOMETHING_WENT_WRONG)
        return False

    if delete_message_id is not None:
        await _try_delete(bot, chat_id, delete_message_id)
    return True


async def answer_voice(message: Message, ctx: HandlerContext) -> None:
    """Отвечает на голосовое сообщение распознанным текстом."""
    if ctx.transcriber is None:
        LOGGER.info("транскрипция выключена — голосовое пропущено")
        return
    try:
        text = await ctx.transcriber(message)
    except Exception as exc:
        LOGGER.exception("ошибка распознавания голосового")
        await message.reply(texts.voice_error(exc))
        return

    await message.reply(texts.voice_result(text) if text else texts.VOICE_EMPTY)


def make_message_handler(
    ctx: HandlerContext,
) -> Callable[[Message, FSMContext], Awaitable[None]]:
    """Личные сообщения: ссылка → диалог, голосовое → расшифровка."""

    async def handler(message: Message, state: FSMContext) -> None:
        if message.text:
            service = services.match(message.text)
            if service is not None:
                await message.answer(**keyboards.download_dialog_message(service.name))
                await state.set_state(DownloadDialog.select_action)
                await state.update_data(
                    url=message.text,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    user_id=message.from_user.id if message.from_user else None,
                )
        if message.voice:
            await answer_voice(message, ctx)

    return handler


def make_callback_handler(
    ctx: HandlerContext,
) -> Callable[[CallbackQuery, FSMContext], Awaitable[None]]:
    """Кнопки Audio/Video/❌ под вопросом «Может качнем…»."""

    async def handler(query: CallbackQuery, state: FSMContext) -> None:
        await query.answer()
        data = await state.get_data() or {}
        await state.clear()

        if query.message is not None:
            await _try_delete(
                query.bot, query.message.chat.id, query.message.message_id
            )

        if query.data == "exit":
            return

        url = data.get("url")
        if not url:
            # состояние потеряно: рестарт бота или нажатие из старого сообщения
            LOGGER.info("нажатие без сохранённой ссылки (устаревший диалог)")
            if query.message is not None:
                await query.message.answer(texts.DIALOG_STALE)
            return

        chat_id = data.get("chat_id")
        if chat_id is None and query.message is not None:
            chat_id = query.message.chat.id
        if chat_id is None:
            LOGGER.warning("не удалось определить чат для %s", url)
            return

        await run_download(
            query.bot,
            ctx,
            chat_id=int(chat_id),
            url=str(url),
            video=query.data == "video",
            user_id=data.get("user_id"),
            delete_message_id=data.get("message_id"),
        )

    return handler


def make_channel_post_handler(ctx: HandlerContext) -> Callable[[Message], Awaitable[None]]:
    """Посты канала: ссылка качается сразу как аудио, без диалога."""

    async def handler(message: Message) -> None:
        if message.text and services.match(message.text) is not None:
            await run_download(
                message.bot,
                ctx,
                chat_id=message.chat.id,
                url=message.text,
                video=False,
                user_id=None,  # посты канала: user_id = 0 в БД (D2)
                delete_message_id=message.message_id,
            )
        if message.voice:
            await answer_voice(message, ctx)

    return handler


def build_router(ctx: HandlerContext) -> Router:
    """Роутер со всеми обработчиками (middleware регистрируется отдельно)."""
    router = Router()
    router.callback_query.register(make_callback_handler(ctx), DownloadDialog.select_action)
    router.message.register(make_message_handler(ctx))
    router.channel_post.register(make_channel_post_handler(ctx))
    return router
