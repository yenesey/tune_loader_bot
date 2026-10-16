"""Тесты раскладки файлов и публичных ссылок (`tune_loader_bot.media.paths`).

Правило `{download-dir}/{YYYY}/{YYYY-MM}/{file_name}` — контракт с веб-сервером
и с уже скачанными ~93 ГБ, поэтому проверяется напрямую, без импорта `bot.py`.
"""

import os
from datetime import date, datetime

import pytest

from tune_loader_bot.media import paths

DOWNLOAD_DIR = "/srv/media"
SERVER_URL = "https://example.test/media"


@pytest.mark.parametrize(
    ("on_date", "expected"),
    [
        (datetime(2026, 10, 16, 12, 30), os.path.join("2026", "2026-10")),
        (datetime(2024, 1, 5), os.path.join("2024", "2024-01")),
        (datetime(2024, 12, 31, 23, 59), os.path.join("2024", "2024-12")),
        (date(2026, 5, 4), os.path.join("2026", "2026-05")),
    ],
)
def test_sub_dir(on_date, expected):
    assert paths.sub_dir(on_date) == expected


def test_target_dir_and_file_path():
    assert str(paths.target_dir(DOWNLOAD_DIR, datetime(2026, 10, 16))) == os.path.join(
        DOWNLOAD_DIR, "2026", "2026-10"
    )
    assert paths.file_path(DOWNLOAD_DIR, datetime(2026, 10, 16), "T.mp3") == os.path.join(
        DOWNLOAD_DIR, "2026", "2026-10", "T.mp3"
    )


def test_public_url_quotes_file_name():
    url = paths.public_url(SERVER_URL, datetime(2026, 10, 16), "Трек с пробелами & #1.mp3")

    assert url.startswith(f"{SERVER_URL}/2026/2026-10/")
    assert " " not in url
    assert "#" not in url
    assert url.endswith(".mp3")


def test_public_url_tolerates_trailing_slash():
    url = paths.public_url(f"{SERVER_URL}/", datetime(2026, 1, 1), "T.mp3")

    assert url == f"{SERVER_URL}/2026/2026-01/T.mp3"


def test_enrich_info_full_metadata():
    info = {
        "artist": "Artist",
        "channel": "Channel",
        "title": "Title",
        "date": datetime(2026, 10, 16, 12, 0),
        "file_name": "Title.mp3",
    }

    result = paths.enrich_info(info, download_dir=DOWNLOAD_DIR, server_root_url=SERVER_URL)

    assert result["performer"] == "Artist"
    assert result["link_text"] == "Artist - Title"
    assert result["server_url"] == f"{SERVER_URL}/2026/2026-10/Title.mp3"
    assert result["file_path"] == os.path.join(DOWNLOAD_DIR, "2026", "2026-10", "Title.mp3")


def test_enrich_info_falls_back_to_channel():
    info = {
        "artist": None,
        "channel": "Channel",
        "title": "T",
        "date": date(2026, 1, 1),
        "file_name": "T.mp3",
    }

    result = paths.enrich_info(info, download_dir=DOWNLOAD_DIR, server_root_url=SERVER_URL)

    assert result["performer"] == "Channel"
    assert result["link_text"] == "Channel - T"


def test_enrich_info_without_performer_uses_title():
    info = {
        "artist": None,
        "channel": None,
        "title": "T",
        "date": date(2026, 1, 1),
        "file_name": "T.mp3",
    }

    result = paths.enrich_info(info, download_dir=DOWNLOAD_DIR, server_root_url=SERVER_URL)

    assert result["performer"] is None
    assert result["link_text"] == "T"


def test_enrich_info_does_not_mutate_input_D20():
    """D20 исправлен: раньше функция дописывала поля прямо во входной dict."""
    info = {
        "artist": "A",
        "channel": None,
        "title": "T",
        "date": date(2026, 1, 1),
        "file_name": "T.mp3",
    }
    snapshot = dict(info)

    result = paths.enrich_info(info, download_dir=DOWNLOAD_DIR, server_root_url=SERVER_URL)

    assert result is not info
    assert info == snapshot, "входной словарь не должен меняться"
    assert {"performer", "link_text", "server_url", "file_path"} <= set(result)


def test_enrich_info_keeps_existing_file_path():
    info = {
        "artist": "A",
        "channel": None,
        "title": "T",
        "date": date(2026, 1, 1),
        "file_name": "T.mp3",
        "file_path": "/custom/path/T.mp3",
    }

    result = paths.enrich_info(info, download_dir=DOWNLOAD_DIR, server_root_url=SERVER_URL)

    assert result["file_path"] == "/custom/path/T.mp3"


@pytest.mark.parametrize("info", [None, {}, "not-a-dict", 42])
def test_enrich_info_returns_none_for_junk(info):
    assert paths.enrich_info(info, download_dir=DOWNLOAD_DIR, server_root_url=SERVER_URL) is None


@pytest.mark.parametrize(
    "info",
    [
        {"file_name": "T.mp3"},
        {"date": date(2026, 1, 1)},
        {"date": None, "file_name": None},
    ],
)
def test_enrich_info_returns_none_without_date_or_file_name(info, caplog):
    """Раньше такие данные валили обработчик `KeyError`-ом."""
    with caplog.at_level("WARNING"):
        result = paths.enrich_info(info, download_dir=DOWNLOAD_DIR, server_root_url=SERVER_URL)

    assert result is None
    assert "enrich_info" in caplog.text


def test_enrich_info_takes_paths_from_settings(project):
    """Если пути не переданы, они берутся из настроек (как в `bot.py`)."""
    import importlib

    importlib.import_module("tune_loader_bot.config").reset_settings_cache()
    info = {
        "artist": "A",
        "channel": None,
        "title": "T",
        "date": date(2026, 1, 1),
        "file_name": "T.mp3",
    }

    result = paths.enrich_info(info)

    assert result["server_url"] == "https://example.test/media/2026/2026-01/T.mp3"
    assert result["file_path"] == str(project / "media" / "2026" / "2026-01" / "T.mp3")


def test_exists_checks_real_file(tmp_path):
    file_path = tmp_path / "T.mp3"
    info = {"file_path": str(file_path)}

    assert paths.exists(info) is False

    file_path.write_bytes(b"x")
    assert paths.exists(info) is True
    assert paths.exists({"file_path": None}) is False
    assert paths.exists(None) is False
