"""Характеризационные тесты определения сервиса по ссылке (D11).

Тесты фиксируют текущее поведение, включая известные дефекты регулярных
выражений: они помечены `_D11` и будут изменены в фазе 4 (реестр сервисов).
"""

import pytest


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://soundcloud.com/artist/track", "soundcloud"),
        ("https://on.soundcloud.com/abc123", "soundcloud"),
        ("https://music.yandex.ru/album/1234/track/5678", "yandex"),
        ("http://music.yandex.by/album/1/track/2", "yandex"),
        ("https://rutube.ru/video/" + "a" * 32 + "/", "rutube"),
        ("https://rutube.ru/play/embed/" + "b" * 32, "rutube"),
        ("https://coub.com/view/1a2b3c", "coub"),
        ("https://www.tiktok.com/@user/video/1234567890", "tiktok"),
        ("https://vt.tiktok.com/ZSabc123/", "tiktok"),
        ("https://www.instagram.com/reel/Cxyz123/", "instagram"),
        ("https://www.instagram.com/p/Cxyz123/", "instagram"),
    ],
)
def test_supported_urls(bot_module, url, expected):
    assert bot_module.check_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/video/1", None),
        ("https://vimeo.com/12345", None),
        ("просто текст без ссылки", None),
        ("", None),
        # Ссылка на канал/плейлист YouTube распознаётся как youtube (в фазе 4
        # решается, нужно ли качать такое как одиночное медиа — D23).
        ("https://www.youtube.com/playlist?list=PL123", "youtube"),
        # http (без s) для YouTube сейчас не распознаётся вовсе.
        ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
    ],
)
def test_unsupported_urls(bot_module, url, expected):
    assert bot_module.check_url(url) == expected


def test_youtube_lookalike_host_matches_D11(bot_module):
    """D11: незаэкранированные точки и необязательный префикс `youtu` дают
    ложное срабатывание на чужом домене. В фазе 4 ожидается None."""
    assert bot_module.check_url("https://youtubeevil.com/watch?v=1") == "youtube"


def test_soundcloud_lookalike_host_matches_D11(bot_module):
    """D11: `(m|on)?.?soundcloud` не проверяет конец хоста, поэтому чужой домен
    с префиксом `soundcloud` считается SoundCloud. В фазе 4 ожидается None."""
    assert bot_module.check_url("https://soundcloud.evil.example/artist/track") == "soundcloud"
