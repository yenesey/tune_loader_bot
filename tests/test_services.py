"""Тесты реестра сервисов (`tune_loader_bot.domain.services`).

Это единственное место, где проверяется распознавание ссылок: раньше регулярки
жили в `bot.py` и не были заякорены (D11), а наборы форматов — строками (D10).
"""

import pytest

from tune_loader_bot.domain import services

YOUTUBE_ID = "dQw4w9WgXcQ"
RUTUBE_ID = "b" * 32


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # youtube
        (f"https://www.youtube.com/watch?v={YOUTUBE_ID}", "youtube"),
        (f"https://youtube.com/watch?v={YOUTUBE_ID}", "youtube"),
        (f"https://youtu.be/{YOUTUBE_ID}", "youtube"),
        (f"https://music.youtube.com/watch?v={YOUTUBE_ID}", "youtube"),
        (f"https://m.youtube.com/watch?v={YOUTUBE_ID}", "youtube"),
        (f"https://www.youtube-nocookie.com/embed/{YOUTUBE_ID}", "youtube"),
        (f"http://www.youtube.com/watch?v={YOUTUBE_ID}", "youtube"),
        ("https://www.youtube.com/playlist?list=PL123", "youtube"),
        ("https://www.youtube.com/@channel/videos", "youtube"),
        # soundcloud
        ("https://soundcloud.com/artist/track", "soundcloud"),
        ("https://on.soundcloud.com/abc123", "soundcloud"),
        ("https://m.soundcloud.com/artist/track", "soundcloud"),
        ("https://snd.sc/abc123", "soundcloud"),
        ("https://soundcloud.app.goo.gl/abc123", "soundcloud"),
        # yandex music
        ("https://music.yandex.ru/album/1234/track/5678", "yandex"),
        ("http://music.yandex.by/album/1/track/2", "yandex"),
        ("https://music.yandex.kz/album/1/track/2", "yandex"),
        ("https://music.yandex.ua/album/1/track/2", "yandex"),
        ("https://music.yandex.com/album/1/track/2", "yandex"),
        # rutube (нужен 32-символьный id)
        (f"https://rutube.ru/video/{RUTUBE_ID}/", "rutube"),
        (f"https://rutube.ru/video/private/{RUTUBE_ID}", "rutube"),
        (f"https://rutube.ru/live/video/{RUTUBE_ID}", "rutube"),
        (f"https://rutube.ru/play/embed/{RUTUBE_ID}", "rutube"),
        (f"https://rutube.ru/embed/{RUTUBE_ID}", "rutube"),
        (f"https://www.rutube.ru/video/{RUTUBE_ID}", "rutube"),
        # coub
        ("https://coub.com/view/1a2b3c", "coub"),
        ("https://coub.com/embed/1a2b3c", "coub"),
        ("https://coub.com/coubs/1a2b3c", "coub"),
        ("coub:1a2b3c", "coub"),
        ("https://c-cdn.coub.com/fb-player.swf?coubID=1a2b3c", "coub"),
        # tiktok
        ("https://www.tiktok.com/@user/video/1234567890", "tiktok"),
        ("https://vt.tiktok.com/ZSabc123/", "tiktok"),
        ("https://vm.tiktok.com/ZSabc123/", "tiktok"),
        ("https://m.tiktok.com/@user/video/1", "tiktok"),
        # instagram
        ("https://www.instagram.com/reel/Cxyz123/", "instagram"),
        ("https://www.instagram.com/reels/Cxyz123/", "instagram"),
        ("https://www.instagram.com/p/Cxyz123/", "instagram"),
        ("https://www.instagram.com/tv/Cxyz123/", "instagram"),
    ],
)
def test_supported_urls(url, expected):
    spec = services.match(url)

    assert spec is not None, f"ссылка не распознана: {url}"
    assert spec.name == expected


@pytest.mark.parametrize(
    "url",
    [
        # D11: чужие домены с «похожими» именами больше не проходят
        "https://youtubeevil.com/watch?v=1",
        "https://notyoutube.com/watch?v=1",
        "https://youtube.com.evil.example/watch?v=1",
        "https://soundcloud.evil.example/artist/track",
        "https://instagram.com.evil.example/p/x",
        "https://tiktok.com.evil.example/@user/video/1",
        "https://rutube.ru.evil.example/video/" + RUTUBE_ID,
        # неподдерживаемые пути и схемы
        "https://rutube.ru/",
        "https://rutube.ru/video/short-id",
        "https://www.instagram.com/share/xyz",
        "https://www.instagram.com/reels/audio/123",
        "https://coub.com/about",
        "ftp://youtube.com/watch?v=1",
        "https://example.com/video/1",
        "https://vimeo.com/12345",
        "просто текст без ссылки",
        "",
        "   ",
        None,
    ],
)
def test_unsupported_urls(url):
    assert services.match(url) is None


def test_service_specs_are_indexed_by_name():
    assert set(services.SERVICES) == {
        "youtube",
        "soundcloud",
        "yandex",
        "rutube",
        "coub",
        "tiktok",
        "instagram",
    }
    assert services.get("youtube") is services.SERVICES["youtube"]
    assert services.get("unknown") is None
    assert services.get(None) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("youtube", ("audio", "video")),
        ("rutube", ("audio", "video")),
        ("coub", ("audio", "video")),
        ("soundcloud", ("audio",)),
        ("yandex", ("audio",)),
        ("tiktok", ("video",)),
        ("instagram", ("video",)),
    ],
)
def test_formats_per_service(name, expected):
    """D10: раньше это были строки `("audio")`, а сравнение — поиском подстроки."""
    assert services.formats(name) == expected
    assert isinstance(services.SERVICES[name].formats, frozenset)


def test_formats_of_unknown_service_raise_key_error():
    with pytest.raises(KeyError):
        services.formats("unknown")
    with pytest.raises(KeyError):
        services.formats(None)


@pytest.mark.parametrize(
    ("name", "video", "expected"),
    [
        ("youtube", True, services.YOUTUBE_VIDEO_FORMAT),
        ("youtube", False, services.YOUTUBE_AUDIO_FORMAT),
        ("soundcloud", False, services.GENERIC_FORMAT),
        ("tiktok", True, services.GENERIC_FORMAT),
        ("unknown", True, services.GENERIC_FORMAT),
        (None, False, services.GENERIC_FORMAT),
    ],
)
def test_format_selector_by_service(name, video, expected):
    assert services.format_selector(name, video) == expected


def test_matching_ignores_trailing_dot_and_case():
    assert services.match("https://WWW.YouTube.COM./watch?v=1").name == "youtube"


def test_bot_check_url_uses_registry(bot_module):
    """`bot.check_url` — тонкая обёртка над реестром (фаза 4)."""
    assert bot_module.check_url("https://youtu.be/abc") == "youtube"
    assert bot_module.check_url("https://youtubeevil.com/watch?v=1") is None
    assert bot_module.check_url("https://soundcloud.evil.example/x") is None
    assert bot_module.check_url("не ссылка") is None
