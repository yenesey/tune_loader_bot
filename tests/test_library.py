"""Тесты медиатеки: готовая запись, загрузка, проверка файла на диске (D7)."""

from datetime import datetime
from pathlib import Path

import pytest
from fakes import make_downloader

from tune_loader_bot.library import MediaLibrary

URL = "https://youtu.be/dQw4w9WgXcQ"
NOW = datetime(2026, 10, 16, 12, 0)


@pytest.fixture
def make_library(settings, storage):
    def build(**kwargs):
        kwargs.setdefault("downloader", make_downloader())
        kwargs.setdefault("now", lambda: NOW)
        return MediaLibrary(settings, storage, **kwargs)

    return build


async def test_find_returns_none_for_empty_database(make_library):
    assert await make_library().find(URL, False, 111) is None


async def test_download_saves_record_and_enriches_it(make_library, settings, storage):
    library = make_library()

    record = await library.download(URL, False, 111)

    assert record["title"] == "Title"
    assert record["performer"] == "Artist"
    assert record["link_text"] == "Artist - Title"
    assert record["server_url"].startswith(settings.server_root_url)
    assert record["file_path"] == str(
        Path(settings.download_dir) / "2026" / "2026-10" / "track.mp3"
    )
    assert record["delivered"] == 0

    saved = await storage.find_download(URL, False, 111)
    assert saved is not None
    assert saved.user_id == 111
    assert saved.delivered is False
    assert saved.date == NOW


async def test_download_creates_date_directory(make_library, settings):
    library = make_library()

    await library.download(URL, False, 111)

    assert (Path(settings.download_dir) / "2026" / "2026-10").is_dir()


async def test_get_or_download_reuses_ready_file(make_library):
    downloader = make_downloader()
    library = make_library(downloader=downloader)

    first = await library.get_or_download(URL, False, 111)
    second = await library.get_or_download(URL, False, 111)

    assert first["file_path"] == second["file_path"]
    assert len(downloader.calls) == 1, "готовый файл не должен качаться заново"


async def test_get_or_download_redownloads_when_file_is_gone(make_library):
    """D7: раньше запись из БД отдавалась даже если файла на диске нет."""
    downloader = make_downloader()
    library = make_library(downloader=downloader)

    record = await library.download(URL, False, 111)
    Path(record["file_path"]).unlink()

    again = await library.get_or_download(URL, False, 111)

    assert again is not None
    assert len(downloader.calls) == 2
    assert Path(again["file_path"]).is_file()


async def test_download_failure_returns_none_and_saves_nothing(make_library, storage):
    library = make_library(downloader=make_downloader(fail=True))

    assert await library.download(URL, False, 111) is None
    assert await storage.count_downloads() == 0


async def test_channel_post_without_user_is_stored_as_zero(make_library, storage):
    library = make_library()

    await library.download(URL, False, None)

    saved = await storage.find_download(URL, False, 0)
    assert saved is not None
    assert saved.user_id == 0


async def test_service_name_is_passed_to_downloader(make_library):
    downloader = make_downloader()
    library = make_library(downloader=downloader)

    await library.download(URL, False, 111)
    await library.download("https://example.com/video/1", False, 111)

    assert [call["service"] for call in downloader.calls] == ["youtube", None]
    assert downloader.calls[0]["video"] is False


async def test_mark_delivered_updates_database(make_library, storage):
    library = make_library()
    record = await library.download(URL, False, 111)

    await library.mark_delivered(record)

    saved = await storage.find_download(URL, False, 111)
    assert saved.delivered is True
    assert record["delivered"] == 1


async def test_video_flag_is_stored_separately(make_library, storage):
    library = make_library()

    await library.download(URL, True, 111)

    assert await storage.find_download(URL, True, 111) is not None
    assert await storage.find_download(URL, False, 111) is None
