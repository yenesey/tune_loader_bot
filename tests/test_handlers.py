"""Тесты обработчиков (фаза 5): диалог, callback, посты канала, голосовые.

Обработчики вызываются напрямую (фабрики `make_*_handler`), с ручными
заглушками aiogram: ни сети, ни токена, ни `Dispatcher.feed_update` не нужно.
"""

from pathlib import Path

import pytest
from fakes import (
    FakeBot,
    FakeCallbackQuery,
    FakeChat,
    FakeMessage,
    FakeState,
    FakeUser,
    FakeVoice,
    make_downloader,
)

from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.telegram import texts
from tune_loader_bot.telegram.delivery import Delivery
from tune_loader_bot.telegram.handlers import (
    HandlerContext,
    make_callback_handler,
    make_channel_post_handler,
    make_message_handler,
    run_download,
)

URL = "https://youtu.be/dQw4w9WgXcQ"


@pytest.fixture
def make_context(settings, storage):
    def build(*, downloader=None, transcriber=None):
        library = MediaLibrary(settings, storage, downloader=downloader or make_downloader())
        return HandlerContext(
            settings=settings,
            library=library,
            delivery=Delivery(settings, library),
            transcriber=transcriber,
        )

    return build


# --- личные сообщения -------------------------------------------------------


async def test_message_with_link_sends_dialog_and_remembers_it(make_context):
    ctx = make_context()
    bot = FakeBot()
    state = FakeState()
    message = FakeMessage(text=URL, from_user=FakeUser(id=111), bot=bot)

    await make_message_handler(ctx)(message, state)

    assert len(message.sent) == 1
    assert message.sent[0]["text"] == texts.DIALOG_QUESTION
    assert state.data["url"] == URL
    assert state.data["chat_id"] == message.chat.id
    assert state.data["message_id"] == message.message_id
    assert state.data["user_id"] == 111


async def test_message_without_link_is_ignored(make_context):
    ctx = make_context()
    message = FakeMessage(text="просто текст", from_user=FakeUser(id=111), bot=FakeBot())
    state = FakeState()

    await make_message_handler(ctx)(message, state)

    assert message.sent == []
    assert state.data == {}


async def test_message_without_from_user_is_handled(make_context):
    """Сообщение без `from_user` не должно падать."""
    ctx = make_context()
    message = FakeMessage(text=URL, from_user=None, bot=FakeBot())
    state = FakeState()

    await make_message_handler(ctx)(message, state)

    assert state.data["user_id"] is None


# --- callback ---------------------------------------------------------------


async def test_callback_downloads_and_edits_placeholder(make_context, settings):
    downloader = make_downloader()
    ctx = make_context(downloader=downloader)
    bot = FakeBot()
    state = FakeState()
    source = FakeMessage(text=URL, from_user=FakeUser(id=111), message_id=7, bot=bot)
    await make_message_handler(ctx)(source, state)

    dialog = FakeMessage(chat=source.chat, message_id=8, bot=bot)
    query = FakeCallbackQuery("audio", bot=bot, message=dialog)

    await make_callback_handler(ctx)(query, state)

    assert query.answered == 1
    assert downloader.calls and downloader.calls[0]["video"] is False
    assert bot.messages[0][1] == texts.PROCESSING
    assert len(bot.sent_messages[0].edited_media) == 1
    # удалены и сообщение-вопрос, и исходное сообщение пользователя
    assert (dialog.chat.id, 8) in bot.deleted
    assert (source.chat.id, 7) in bot.deleted
    assert state.data == {}, "состояние диалога должно очищаться"


async def test_callback_video_sets_video_flag(make_context):
    downloader = make_downloader()
    ctx = make_context(downloader=downloader)
    bot = FakeBot()
    state = FakeState()
    source = FakeMessage(text=URL, from_user=FakeUser(id=111), bot=bot)
    await make_message_handler(ctx)(source, state)

    query = FakeCallbackQuery(
        "video", bot=bot, message=FakeMessage(chat=source.chat, message_id=8, bot=bot)
    )
    await make_callback_handler(ctx)(query, state)

    assert downloader.calls[0]["video"] is True


async def test_callback_exit_downloads_nothing(make_context):
    downloader = make_downloader()
    ctx = make_context(downloader=downloader)
    bot = FakeBot()
    state = FakeState()
    source = FakeMessage(text=URL, from_user=FakeUser(id=111), bot=bot)
    await make_message_handler(ctx)(source, state)
    dialog = FakeMessage(chat=source.chat, message_id=8, bot=bot)

    await make_callback_handler(ctx)(FakeCallbackQuery("exit", bot=bot, message=dialog), state)

    assert downloader.calls == []
    assert bot.messages == []
    assert (dialog.chat.id, 8) in bot.deleted


async def test_stale_callback_answers_politely_D9(make_context):
    """D9: раньше потерянное состояние (рестарт) давало `KeyError`."""
    downloader = make_downloader()
    ctx = make_context(downloader=downloader)
    bot = FakeBot()
    state = FakeState()  # данных нет: бот перезапускался
    dialog = FakeMessage(chat=FakeChat(id=111), message_id=8, bot=bot)

    await make_callback_handler(ctx)(FakeCallbackQuery("audio", bot=bot, message=dialog), state)

    assert downloader.calls == []
    assert dialog.sent and dialog.sent[0]["text"] == texts.DIALOG_STALE


# --- посты канала -----------------------------------------------------------


async def test_channel_post_downloads_audio_for_service_user(make_context, storage):
    ctx = make_context()
    bot = FakeBot()
    post = FakeMessage(text=URL, from_user=None, message_id=5, bot=bot)

    await make_channel_post_handler(ctx)(post)

    assert len(bot.sent_messages[0].edited_media) == 1
    assert (post.chat.id, 5) in bot.deleted
    saved = await storage.find_download(URL, False, 0)
    assert saved is not None and saved.user_id == 0


async def test_channel_post_without_link_is_ignored(make_context):
    ctx = make_context()
    bot = FakeBot()

    await make_channel_post_handler(ctx)(FakeMessage(text="без ссылки", bot=bot))

    assert bot.messages == []


# --- голосовые --------------------------------------------------------------


def voice_message(bot: FakeBot) -> FakeMessage:
    return FakeMessage(voice=FakeVoice(), from_user=FakeUser(id=111), bot=bot)


async def test_voice_is_transcribed(make_context):
    async def transcriber(message):
        return "привет мир"

    ctx = make_context(transcriber=transcriber)
    message = voice_message(FakeBot())

    await make_message_handler(ctx)(message, FakeState())

    assert message.replies == [texts.voice_result("привет мир")]


async def test_voice_without_speech_reports_empty(make_context):
    async def transcriber(message):
        return None

    ctx = make_context(transcriber=transcriber)
    message = voice_message(FakeBot())

    await make_message_handler(ctx)(message, FakeState())

    assert message.replies == [texts.VOICE_EMPTY]


async def test_voice_error_is_reported(make_context):
    async def transcriber(message):
        raise RuntimeError("whisper упал")

    ctx = make_context(transcriber=transcriber)
    message = voice_message(FakeBot())

    await make_message_handler(ctx)(message, FakeState())

    assert message.replies == [texts.voice_error("whisper упал")]


async def test_voice_is_skipped_without_transcriber(make_context):
    ctx = make_context(transcriber=None)
    message = voice_message(FakeBot())

    await make_message_handler(ctx)(message, FakeState())

    assert message.replies == []


# --- ошибки и границы -------------------------------------------------------


async def test_download_failure_reports_problem(make_context):
    ctx = make_context(downloader=make_downloader(fail=True))
    bot = FakeBot()

    result = await run_download(bot, ctx, chat_id=111, url=URL, video=False, user_id=111)

    assert result is False
    assert bot.messages[-1][1] == texts.SOMETHING_WENT_WRONG
    assert bot.sent_messages[0].edited_media == []


async def test_big_file_is_sent_as_link(make_context, settings):
    ctx = make_context(
        downloader=make_downloader(payload={"file_size": settings.tg_max_file_size + 1})
    )
    bot = FakeBot()

    result = await run_download(bot, ctx, chat_id=111, url=URL, video=False, user_id=111)

    assert result is True
    placeholder = bot.sent_messages[0]
    assert placeholder.edited_media == []
    assert len(placeholder.edited_text) == 1


async def test_missing_file_reports_problem(make_context, tmp_path):
    ctx = make_context(
        downloader=make_downloader(
            payload={"file_path": str(tmp_path / "absent.mp3"), "file_size": None}
        )
    )
    bot = FakeBot()

    result = await run_download(bot, ctx, chat_id=111, url=URL, video=False, user_id=111)

    assert result is False
    assert bot.messages[-1][1] == texts.SOMETHING_WENT_WRONG


async def test_delete_failure_is_not_reported_as_error(make_context):
    """Ошибка удаления сообщения больше не превращается в «Something went wrong…»."""
    ctx = make_context()
    bot = FakeBot(fail_delete=True)

    result = await run_download(
        bot, ctx, chat_id=111, url=URL, video=False, user_id=111, delete_message_id=7
    )

    assert result is True
    assert bot.deleted == []
    assert texts.SOMETHING_WENT_WRONG not in [text for _, text in bot.messages]


async def test_download_crash_is_reported(make_context):
    async def exploding_downloader(target, service, url, video):
        raise RuntimeError("boom")

    ctx = make_context(downloader=exploding_downloader)
    bot = FakeBot()

    result = await run_download(bot, ctx, chat_id=111, url=URL, video=False, user_id=111)

    assert result is False
    assert bot.messages[-1][1] == texts.SOMETHING_WENT_WRONG


async def test_saved_file_lands_in_download_dir(make_context, settings):
    ctx = make_context()

    await run_download(FakeBot(), ctx, chat_id=111, url=URL, video=False, user_id=111)

    assert list(Path(settings.download_dir).rglob("track.mp3"))
