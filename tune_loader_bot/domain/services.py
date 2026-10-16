"""Реестр поддерживаемых сервисов.

Единственный источник правды о сервисах: какие хосты к нему относятся, какие
форматы доступны и какой селектор формата нужен yt-dlp. Раньше это знание было
размазано: `bot.py` (`check_url` с незаякоренными regex'ами и
`create_download_dialog` со строками `("audio")` вместо кортежей) и `download.py`
(селектор формата по имени сервиса).

Исправления:

* **D11** — матчинг идёт по разобранному `urlsplit`-ом хосту и точным доменам,
  поэтому `https://youtubeevil.com/...` и `https://soundcloud.evil.example/...`
  больше не распознаются как сервисы; перестали быть нужны незаэкранированные
  точки и «магические» префиксы. Побочно теперь распознаются `http://`-ссылки,
  `m.youtube.com`, `snd.sc` (раньше — нет);
* **D10** — набор форматов это `frozenset`, а не строка-подстрока.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import SplitResult, urlsplit

AUDIO = "audio"
VIDEO = "video"

#: Порядок кнопок в диалоге и в ответах реестра.
FORMAT_ORDER = (AUDIO, VIDEO)

YOUTUBE_VIDEO_FORMAT = "(mp4,webm)[height<960]"
YOUTUBE_AUDIO_FORMAT = "bestaudio/best"
GENERIC_FORMAT = "bestvideo*+bestaudio/best"


def _host_matches(host: str, domains: Iterable[str]) -> bool:
    """Хост совпадает с доменом или является его поддоменом."""
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """Описание сервиса."""

    name: str
    domains: tuple[str, ...]
    formats: frozenset[str]
    path_pattern: re.Pattern[str] | None = None
    scheme: str | None = None
    video_format: str = GENERIC_FORMAT
    audio_format: str = GENERIC_FORMAT

    def matches(self, parts: SplitResult) -> bool:
        scheme = (parts.scheme or "").lower()
        if self.scheme and scheme == self.scheme:
            # шорткат вида `coub:1a2b3c`
            return bool(parts.path.strip("/"))
        if scheme not in ("http", "https"):
            return False
        if not _host_matches(parts.hostname or "", self.domains):
            return False
        if self.path_pattern is not None and not self.path_pattern.search(parts.path or ""):
            return False
        return True

    def format_selector(self, video: bool) -> str:
        """Селектор формата для yt-dlp."""
        return self.video_format if video else self.audio_format


SERVICE_SPECS: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        name="youtube",
        domains=("youtube.com", "youtu.be", "youtube-nocookie.com"),
        formats=frozenset({AUDIO, VIDEO}),
        video_format=YOUTUBE_VIDEO_FORMAT,
        audio_format=YOUTUBE_AUDIO_FORMAT,
    ),
    ServiceSpec(
        name="soundcloud",
        domains=("soundcloud.com", "snd.sc", "soundcloud.app.goo.gl"),
        formats=frozenset({AUDIO}),
    ),
    ServiceSpec(
        name="yandex",
        domains=(
            "music.yandex.ru",
            "music.yandex.kz",
            "music.yandex.ua",
            "music.yandex.by",
            "music.yandex.com",
        ),
        formats=frozenset({AUDIO}),
    ),
    ServiceSpec(
        name="rutube",
        domains=("rutube.ru",),
        formats=frozenset({AUDIO, VIDEO}),
        # rutube отдаёт только страницы видео/embed с 32-символьным id
        path_pattern=re.compile(
            r"^/(?:(?:live/)?video(?:/private)?|(?:play/)?embed)/[\da-z]{32}", re.IGNORECASE
        ),
    ),
    ServiceSpec(
        name="coub",
        domains=("coub.com", "c-cdn.coub.com"),
        formats=frozenset({AUDIO, VIDEO}),
        scheme="coub",
        path_pattern=re.compile(r"^/(?:view|embed|coubs)/[\da-z]+|/fb-player\.swf", re.IGNORECASE),
    ),
    ServiceSpec(
        name="tiktok",
        domains=("tiktok.com",),
        formats=frozenset({VIDEO}),
    ),
    ServiceSpec(
        name="instagram",
        domains=("instagram.com",),
        formats=frozenset({VIDEO}),
        # посты, tv и reels; страницы /reels/audio/... и /share/... не качаются
        path_pattern=re.compile(r"^/(?:p|tv|reels?)/(?!audio/)[^/?#&]+", re.IGNORECASE),
    ),
)

#: Доступ по имени: `SERVICES["youtube"]`.
SERVICES: dict[str, ServiceSpec] = {spec.name: spec for spec in SERVICE_SPECS}


def match(url: str | None) -> ServiceSpec | None:
    """Определяет сервис по ссылке. `None` — ссылка не поддерживается."""
    if not url:
        return None
    text = url.strip()
    if not text:
        return None
    parts = urlsplit(text)
    for spec in SERVICE_SPECS:
        if spec.matches(parts):
            return spec
    return None


def get(name: str | None) -> ServiceSpec | None:
    """Сервис по имени (для yt-dlp нужен только селектор формата)."""
    return SERVICES.get(name) if name else None


def formats(name: str | None) -> tuple[str, ...]:
    """Форматы сервиса в порядке кнопок. `KeyError` — если сервиса нет."""
    spec = SERVICES.get(name) if name else None
    if spec is None:
        raise KeyError(name)
    return tuple(fmt for fmt in FORMAT_ORDER if fmt in spec.formats)


def format_selector(name: str | None, video: bool) -> str:
    """Селектор формата по имени сервиса (неизвестный сервис — общий)."""
    spec = get(name)
    return spec.format_selector(video) if spec else GENERIC_FORMAT
