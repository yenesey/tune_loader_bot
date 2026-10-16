"""Тесты доставки: размеры, подписи, отметка `delivered` (D6/D21)."""

from pathlib import Path

import pytest
from aiogram.types import InputMediaAudio, InputMediaVideo
from fakes import FakeBot, FakeMessage, make_downloader, make_record

from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.telegram.delivery import Delivery


@pytest.fixture
def delivery_and_library(settings, storage):
    library = MediaLibrary(settings, storage, downloader=make_downloader())
    return Delivery(settings, library), library


def test_size_from_record(delivery_and_library):
    delivery, _ = delivery_and_library

    assert delivery.size_of(make_record(delivery._settings, file_size=123)) == 123


def test_size_falls_back_to_disk(delivery_and_library, tmp_path):
    delivery, _ = delivery_and_library
    path = tmp_path / "track.mp3"
    path.write_bytes(b"0123456789")

    record = make_record(delivery._settings, file_size=None, file_path=str(path))

    assert delivery.size_of(record) == 10


def test_size_is_none_without_file(delivery_and_library, tmp_path):
    delivery, _ = delivery_and_library

    record = make_record(
        delivery._settings, file_size=None, file_path=str(tmp_path / "absent.mp3")
    )

    assert delivery.size_of(record) is None
    assert delivery.fits_telegram(record) is False


def test_fits_telegram_boundary(delivery_and_library):
    delivery, _ = delivery_and_library
    limit = delivery._settings.tg_max_file_size

    assert delivery.fits_telegram(make_record(delivery._settings, file_size=limit - 1))
    assert not delivery.fits_telegram(make_record(delivery._settings, file_size=limit))


def test_media_object_depends_on_video_flag(delivery_and_library):
    delivery, _ = delivery_and_library

    audio = delivery.media(make_record(delivery._settings, video=0))
    video = delivery.media(make_record(delivery._settings, video=1))

    assert isinstance(audio, InputMediaAudio)
    assert isinstance(video, InputMediaVideo)
    assert audio.title == "Title"
    assert audio.performer == "Artist"
    assert "#origin" in audio.caption and "#file" in audio.caption


async def test_send_inline_edits_media_for_small_file(delivery_and_library):
    delivery, _ = delivery_and_library
    placeholder = FakeMessage()

    assert await delivery.send_inline(placeholder, make_record(delivery._settings)) is True

    assert len(placeholder.edited_media) == 1
    assert placeholder.edited_text == []


async def test_send_inline_sends_link_for_big_file(delivery_and_library):
    delivery, _ = delivery_and_library
    placeholder = FakeMessage()
    record = make_record(delivery._settings, file_size=delivery._settings.tg_max_file_size + 1)

    assert await delivery.send_inline(placeholder, record) is True

    assert placeholder.edited_media == []
    assert len(placeholder.edited_text) == 1
    assert record["server_url"] in placeholder.edited_text[0]


async def test_send_inline_returns_false_without_file(delivery_and_library, tmp_path, caplog):
    delivery, _ = delivery_and_library
    placeholder = FakeMessage()
    record = make_record(
        delivery._settings, file_size=None, file_path=str(tmp_path / "absent.mp3")
    )

    with caplog.at_level("ERROR"):
        assert await delivery.send_inline(placeholder, record) is False

    assert placeholder.edited_media == [] and placeholder.edited_text == []
    assert "нечего отправлять" in caplog.text


async def test_send_to_chat_marks_delivered(delivery_and_library, storage):
    delivery, library = delivery_and_library
    bot = FakeBot()
    record = await library.download("https://youtu.be/x", False, 111)

    assert await delivery.send_to_chat(bot, 111, record) is True

    assert bot.audio and bot.audio[0][0] == 111
    saved = await storage.find_download("https://youtu.be/x", False, 111)
    assert saved.delivered is True


async def test_send_to_chat_sends_link_for_big_file(delivery_and_library, storage):
    delivery, library = delivery_and_library
    bot = FakeBot()
    record = await library.download("https://youtu.be/x", False, 111)
    record["file_size"] = delivery._settings.tg_max_file_size + 1

    assert await delivery.send_to_chat(bot, 111, record) is True

    assert bot.audio == []
    assert bot.messages and bot.messages[0][0] == 111


async def test_send_to_chat_does_nothing_without_file(delivery_and_library, storage, tmp_path):
    delivery, library = delivery_and_library
    bot = FakeBot()
    record = await library.download("https://youtu.be/x", False, 111)
    Path(record["file_path"]).unlink()
    record["file_size"] = None

    assert await delivery.send_to_chat(bot, 111, record) is False

    assert bot.audio == [] and bot.messages == []
    saved = await storage.find_download("https://youtu.be/x", False, 111)
    assert saved.delivered is False
