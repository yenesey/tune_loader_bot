"""Тесты обходной загрузки Яндекс.Музыки: сеть подменяется FakeOpener."""

import hashlib
import io
import json
import urllib.error

import pytest

from tune_loader_bot.media import yandex

TRACK_ID = "84520196"
URL = f"https://music.yandex.ru/album/16031075/track/{TRACK_ID}"
API_TRACK = f"https://api.music.yandex.ru/tracks/{TRACK_ID}"
API_INFO = f"{API_TRACK}/download-info"
INFO_URL = "https://storage.example/download-info"
MP3_BYTES = b"ID3-fake-mp3-bytes"

TRACK_JSON = json.dumps(
    {
        "result": [
            {
                "id": TRACK_ID,
                "title": "Astral Asteroid",
                "available": True,
                "artists": [{"name": "Richard Stonefield"}],
                "albums": [{"title": "Stardust Adventure"}],
            }
        ]
    }
).encode()

INFO_JSON = json.dumps(
    {
        "result": [
            {
                "codec": "mp3",
                "bitrateInKbps": 128,
                "preview": True,
                "downloadInfoUrl": "https://storage.example/preview",
            },
            {
                "codec": "mp3",
                "bitrateInKbps": 192,
                "preview": False,
                "downloadInfoUrl": INFO_URL,
            },
        ]
    }
).encode()

XML = (
    b"<download-info><host>storage.example</host><path>/get/path.mp3</path>"
    b"<ts>1700000000</ts><s>secret</s></download-info>"
)
KEY = hashlib.md5(
    (yandex._DOWNLOAD_SECRET + "get/path.mp3" + "secret").encode()
).hexdigest()
MP3_URL = (
    f"https://storage.example/get-mp3/{KEY}/1700000000/get/path.mp3"
    f"?track-id={TRACK_ID}"
)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._position = 0

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            data = self._body[self._position :]
            self._position = len(self._body)
            return data
        data = self._body[self._position : self._position + size]
        self._position += len(data)
        return data

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class FakeOpener:
    """Минимальная замена `OpenerDirector`: URL → тело ответа или исключение."""

    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append(url)
        value = self.responses.get(url)
        if value is None:
            raise AssertionError(f"неожиданный URL: {url}")
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


def make_opener(**overrides) -> FakeOpener:
    responses = {
        API_TRACK: TRACK_JSON,
        API_INFO: INFO_JSON,
        INFO_URL: XML,
        MP3_URL: MP3_BYTES,
    }
    responses.update(overrides)
    return FakeOpener(responses)


def test_parse_track_id_variants():
    assert yandex.parse_track_id(URL) == TRACK_ID
    assert yandex.parse_track_id(f"https://music.yandex.ru/track/{TRACK_ID}") == TRACK_ID
    assert yandex.parse_track_id(f"https://music.yandex.com/tracks/{TRACK_ID}/") == TRACK_ID
    assert yandex.parse_track_id("https://music.yandex.ru/album/1") is None
    assert yandex.parse_track_id("https://music.yandex.ru/playlists/1") is None


async def test_download_full_track(settings, tmp_path):
    opener = make_opener()

    result = await yandex.download_media(
        settings, tmp_path / "media", URL, opener=opener
    )

    assert result is not None
    assert result.file_name == "Astral Asteroid.mp3"
    assert result.title == "Astral Asteroid"
    assert result.artist == "Richard Stonefield"
    assert result.channel is None
    assert result.file_size == len(MP3_BYTES)
    assert (tmp_path / "media" / result.file_name).read_bytes() == MP3_BYTES
    assert opener.requests == [API_TRACK, API_INFO, INFO_URL, MP3_URL]


async def test_filename_template_is_applied(settings, tmp_path):
    configured = settings.model_copy(
        update={"filename_template": "%(artist)s - %(title)s [%(id)s]"}
    )
    opener = make_opener()

    result = await yandex.download_media(
        configured, tmp_path / "media", URL, opener=opener
    )

    assert result is not None
    assert result.file_name == "Richard Stonefield - Astral Asteroid [84520196].mp3"


async def test_preview_only_returns_none(settings, tmp_path, caplog):
    preview = json.dumps(
        {
            "result": [
                {
                    "codec": "mp3",
                    "bitrateInKbps": 128,
                    "preview": True,
                    "downloadInfoUrl": "https://storage.example/preview",
                }
            ]
        }
    ).encode()
    opener = make_opener(**{API_INFO: preview})

    with caplog.at_level("ERROR"):
        result = await yandex.download_media(
            settings, tmp_path / "media", URL, opener=opener
        )

    assert result is None
    assert "только превью" in caplog.text
    assert opener.requests == [API_TRACK, API_INFO]


async def test_unavailable_track_returns_none(settings, tmp_path, caplog):
    track = json.dumps({"result": [{"available": False}]}).encode()

    with caplog.at_level("ERROR"):
        result = await yandex.download_media(
            settings, tmp_path / "media", URL, opener=make_opener(**{API_TRACK: track})
        )

    assert result is None
    assert "трек недоступен" in caplog.text


async def test_missing_cookies_returns_none(settings, tmp_path, caplog):
    with caplog.at_level("ERROR"):
        result = await yandex.download_media(settings, tmp_path / "media", URL)

    assert result is None
    assert "файл cookies не найден" in caplog.text


def test_load_opener_reads_cookies(settings, tmp_path):
    (tmp_path / "cookies.txt").write_text(
        "# Netscape HTTP Cookie File\n"
        ".yandex.ru\tTRUE\t/\tFALSE\t0\tSession_id\tsecret\n",
        encoding="utf-8",
    )

    assert yandex._load_opener(settings) is not None


def test_load_opener_rejects_bad_cookies(settings, tmp_path):
    (tmp_path / "cookies.txt").write_text("not a cookie file", encoding="utf-8")

    with pytest.raises(yandex.YandexError, match="не удалось прочитать cookies"):
        yandex._load_opener(settings)


async def test_api_http_error_returns_none(settings, tmp_path, caplog):
    error = urllib.error.HTTPError(
        API_TRACK,
        401,
        "Unauthorized",
        None,
        io.BytesIO(b'{"error":"Unauthorized"}'),
    )

    with caplog.at_level("ERROR"):
        result = await yandex.download_media(
            settings, tmp_path / "media", URL, opener=make_opener(**{API_TRACK: error})
        )

    assert result is None
    assert "HTTP 401" in caplog.text


async def test_invalid_json_returns_none(settings, tmp_path, caplog):
    with caplog.at_level("ERROR"):
        result = await yandex.download_media(
            settings,
            tmp_path / "media",
            URL,
            opener=make_opener(**{API_TRACK: b"not json"}),
        )

    assert result is None
    assert "не является JSON" in caplog.text


async def test_unexpected_error_is_logged(settings, tmp_path, caplog):
    with caplog.at_level("ERROR"):
        result = await yandex.download_media(
            settings,
            tmp_path / "media",
            URL,
            opener=make_opener(**{API_TRACK: RuntimeError("boom")}),
        )

    assert result is None
    assert "неожиданная ошибка" in caplog.text
    assert "RuntimeError" in caplog.text


async def test_empty_file_returns_none(settings, tmp_path, caplog):
    with caplog.at_level("ERROR"):
        result = await yandex.download_media(
            settings,
            tmp_path / "media",
            URL,
            opener=make_opener(**{MP3_URL: b""}),
        )

    assert result is None
    assert "пустой файл" in caplog.text
    assert not (tmp_path / "media" / "Astral Asteroid.mp3").exists()


async def test_template_escaping_target_falls_back_to_id(settings, tmp_path):
    configured = settings.model_copy(update={"filename_template": "../outside"})
    opener = make_opener()

    result = await yandex.download_media(
        configured, tmp_path / "media", URL, opener=opener
    )

    assert result is not None
    assert result.file_name == f"{TRACK_ID}.mp3"
    assert (tmp_path / "media" / result.file_name).is_file()
    assert not (tmp_path / "outside").exists()


def test_select_download_info_prefers_full_mp3():
    infos = [
        {"codec": "mp3", "bitrateInKbps": 128, "preview": True, "downloadInfoUrl": "u"},
        {"codec": "aac", "bitrateInKbps": 256, "preview": False, "downloadInfoUrl": "u"},
        {"codec": "mp3", "bitrateInKbps": 192, "preview": False, "downloadInfoUrl": "u"},
        {"codec": "mp3", "bitrateInKbps": 320, "preview": False},
    ]

    assert yandex.select_download_info(infos)["bitrateInKbps"] == 192


def test_select_download_info_none_for_preview():
    assert (
        yandex.select_download_info(
            [{"codec": "mp3", "preview": True, "downloadInfoUrl": "u"}]
        )
        is None
    )


def test_build_download_url_rejects_incomplete_xml():
    opener = FakeOpener({INFO_URL: b"<download-info><host>h</host></download-info>"})

    with pytest.raises(yandex.YandexError, match="host/path/ts/s"):
        yandex.build_download_url(opener, {"downloadInfoUrl": INFO_URL}, TRACK_ID)


def test_build_download_url_rejects_non_https():
    with pytest.raises(yandex.YandexError, match="некорректная ссылка"):
        yandex.build_download_url({}, {"downloadInfoUrl": "http://x"}, TRACK_ID)
