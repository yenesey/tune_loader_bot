"""Тесты загрузки через yt-dlp: поддельная фабрика `YoutubeDL`, сеть не нужна."""

import threading
import time
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from tune_loader_bot.config import Settings
from tune_loader_bot.media import ytdlp

MEDIA_BYTES = b"media-bytes"


class ConcurrencyTracker:
    """Считает одновременные входы в `extract_info`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def enter(self, url: str) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def exit(self, url: str) -> None:
        with self._lock:
            self.active -= 1


class FakeYdl:
    """Минимальная замена `YoutubeDL`."""

    def __init__(self, options, *, info, error, file_name, create_file, on_call, delay, tracker):
        self.options = options
        self._info = info
        self._error = error
        self._file_name = file_name
        self._create_file = create_file
        self._on_call = on_call
        self._delay = delay
        self._tracker = tracker

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download):
        if self._tracker:
            self._tracker.enter(url)
        try:
            if self._delay:
                time.sleep(self._delay)
            if self._error:
                raise self._error
            if self._on_call:
                self._on_call(self.options, url)
            if self._create_file:
                path = Path(self.options["paths"]["home"]) / self._file_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(MEDIA_BYTES)
            return self._info
        finally:
            if self._tracker:
                self._tracker.exit(url)


def make_factory(
    *,
    info=None,
    error=None,
    file_name="track.mp3",
    create_file=True,
    on_call=None,
    delay=0.0,
    tracker=None,
):
    calls = []

    def factory(options):
        calls.append(options)
        return FakeYdl(
            options,
            info=info,
            error=error,
            file_name=file_name,
            create_file=create_file,
            on_call=on_call,
            delay=delay,
            tracker=tracker,
        )

    return factory, calls


def make_settings(tmp_path, **overrides) -> Settings:
    data = {
        "telegram-api-token": "t",
        "download-dir": str(tmp_path / "media"),
        "server-root-url": "https://example.test/media",
        "users-list": [111],
        "cookies-file": str(tmp_path / "cookies.txt"),
        "deno-path": "/usr/local/bin/deno",
    }
    data.update(overrides)
    return Settings.model_validate(data)


@pytest.fixture(autouse=True)
def _reset_limits():
    ytdlp.reset_limits()
    yield
    ytdlp.reset_limits()


async def test_successful_download_returns_result(tmp_path):
    settings = make_settings(tmp_path)
    target = tmp_path / "target"
    file_path = target / "track.mp3"
    factory, calls = make_factory(
        info={"filepath": str(file_path), "title": "Title", "artist": "Artist", "channel": "Channel"}
    )

    result = await ytdlp.download_media(
        settings, target, "youtube", "https://youtu.be/x", ydl_factory=factory
    )

    assert result is not None
    assert result.file_name == "track.mp3"
    assert result.file_path == str(file_path)
    assert result.file_size == len(MEDIA_BYTES)
    assert (result.title, result.artist, result.channel) == ("Title", "Artist", "Channel")
    assert result.as_dict()["file_name"] == "track.mp3"
    # опции, с которыми запускался yt-dlp
    assert calls[0]["paths"]["home"] == str(target)
    assert calls[0]["noplaylist"] is True


async def test_target_dir_is_created(tmp_path):
    settings = make_settings(tmp_path)
    target = tmp_path / "deep" / "nested"
    factory, _ = make_factory(info=None, create_file=False)

    await ytdlp.download_media(settings, target, "youtube", "https://youtu.be/x", ydl_factory=factory)

    assert target.is_dir()


async def test_result_is_resolved_from_move_files_hook(tmp_path):
    """Файл находится по хуку, даже если `info` не содержит `filepath`."""
    settings = make_settings(tmp_path)
    target = tmp_path / "target"
    file_path = target / "hooked.mp3"

    def on_call(options, url):
        hook = options["postprocessor_hooks"][0]
        hook(
            {
                "info_dict": {"filepath": str(file_path), "title": "Hooked", "uploader": "Uploader"},
                "postprocessor": "MoveFiles",
                "status": "finished",
            }
        )

    factory, _ = make_factory(info={}, file_name="hooked.mp3", on_call=on_call)

    result = await ytdlp.download_media(
        settings, target, "youtube", "https://youtu.be/x", ydl_factory=factory
    )

    assert result is not None
    assert result.file_name == "hooked.mp3"
    assert result.title == "Hooked"
    assert result.channel == "Uploader"


async def test_hook_with_missing_file_info_does_not_crash_D6(tmp_path, caplog):
    """D6: `filepath = None` в хуке больше не роняет загрузку."""
    settings = make_settings(tmp_path)
    target = tmp_path / "target"

    def on_call(options, url):
        hook = options["postprocessor_hooks"][0]
        hook({"info_dict": {}, "postprocessor": "MoveFiles", "status": "finished"})
        hook({"info_dict": {"filepath": None}, "postprocessor": "MoveFiles", "status": "finished"})

    factory, _ = make_factory(info={}, create_file=False, on_call=on_call)

    with caplog.at_level("ERROR"):
        result = await ytdlp.download_media(
            settings, target, "youtube", "https://youtu.be/x", ydl_factory=factory
        )

    assert result is None
    assert "не вернул путь к файлу" in caplog.text


async def test_media_result_is_none_when_file_is_absent_D6(tmp_path, caplog):
    """Путь есть, а файла на диске нет (например, MoveFiles не сработал)."""
    settings = make_settings(tmp_path)
    target = tmp_path / "target"
    factory, _ = make_factory(info={"filepath": str(target / "gone.mp3")}, create_file=False)

    with caplog.at_level("ERROR"):
        result = await ytdlp.download_media(
            settings, target, "youtube", "https://youtu.be/x", ydl_factory=factory
        )

    assert result is None
    assert "не вернул путь к файлу" in caplog.text


@pytest.mark.parametrize("error", [DownloadError("boom"), OSError("no space")])
async def test_known_errors_are_swallowed(tmp_path, caplog, error):
    settings = make_settings(tmp_path)
    factory, _ = make_factory(error=error, create_file=False)

    with caplog.at_level("ERROR"):
        result = await ytdlp.download_media(
            settings, tmp_path / "target", "youtube", "https://youtu.be/x", ydl_factory=factory
        )

    assert result is None
    assert "ошибка" in caplog.text


async def test_unexpected_error_is_logged_with_traceback(tmp_path, caplog):
    settings = make_settings(tmp_path)
    factory, _ = make_factory(error=RuntimeError("bug"), create_file=False)

    with caplog.at_level("ERROR"):
        result = await ytdlp.download_media(
            settings, tmp_path / "target", "youtube", "https://youtu.be/x", ydl_factory=factory
        )

    assert result is None
    assert "неожиданная ошибка" in caplog.text
    assert "RuntimeError" in caplog.text


async def test_playlist_extraction_filters_entries(tmp_path):
    settings = make_settings(tmp_path)
    entries = [
        {"title": "one", "url": "https://youtu.be/1"},
        None,
        "junk",
        {"title": "two", "url": "https://youtu.be/2"},
    ]
    factory, calls = make_factory(info={"entries": entries})

    result = await ytdlp.download_playlist(settings, "https://youtube.com/@channel/videos", items="1,2", ydl_factory=factory)

    assert [entry["title"] for entry in result] == ["one", "two"]
    assert calls[0]["skip_download"] is True
    assert calls[0]["playlist_items"] == "1,2"
    assert calls[0]["extract_flat"] == "in_playlist"


@pytest.mark.parametrize("info", [None, {}, {"entries": None}, {"entries": []}])
async def test_playlist_without_entries_returns_empty_D24(tmp_path, info):
    settings = make_settings(tmp_path)
    factory, _ = make_factory(info=info, create_file=False)

    assert await ytdlp.download_playlist(settings, "https://youtube.com/@channel", ydl_factory=factory) == []


async def test_playlist_error_returns_empty(tmp_path, caplog):
    settings = make_settings(tmp_path)
    factory, _ = make_factory(error=RuntimeError("boom"), create_file=False)

    with caplog.at_level("ERROR"):
        result = await ytdlp.download_playlist(settings, "https://youtube.com/@channel", ydl_factory=factory)

    assert result == []
    assert "не удалось разобрать плейлист" in caplog.text


async def test_same_url_downloads_are_serialized(tmp_path):
    """Две одновременные загрузки одной ссылки не идут параллельно."""
    import asyncio

    settings = make_settings(tmp_path, **{"max-concurrent-downloads": 2})
    tracker = ConcurrencyTracker()
    factory, _ = make_factory(info={}, create_file=False, delay=0.05, tracker=tracker)

    await asyncio.gather(
        ytdlp.download_media(settings, tmp_path / "t", "youtube", "https://youtu.be/same", ydl_factory=factory),
        ytdlp.download_media(settings, tmp_path / "t", "youtube", "https://youtu.be/same", ydl_factory=factory),
    )

    assert tracker.max_active == 1


async def test_different_urls_respect_concurrency_limit(tmp_path):
    import asyncio

    limited = make_settings(tmp_path, **{"max-concurrent-downloads": 1})
    tracker = ConcurrencyTracker()
    factory, _ = make_factory(info={}, create_file=False, delay=0.05, tracker=tracker)

    await asyncio.gather(
        ytdlp.download_media(limited, tmp_path / "t", "youtube", "https://youtu.be/a", ydl_factory=factory),
        ytdlp.download_media(limited, tmp_path / "t", "youtube", "https://youtu.be/b", ydl_factory=factory),
    )

    assert tracker.max_active == 1

    parallel = make_settings(tmp_path, **{"max-concurrent-downloads": 2})
    tracker2 = ConcurrencyTracker()
    factory2, _ = make_factory(info={}, create_file=False, delay=0.05, tracker=tracker2)

    await asyncio.gather(
        ytdlp.download_media(parallel, tmp_path / "t", "youtube", "https://youtu.be/c", ydl_factory=factory2),
        ytdlp.download_media(parallel, tmp_path / "t", "youtube", "https://youtu.be/d", ydl_factory=factory2),
    )

    assert tracker2.max_active == 2
