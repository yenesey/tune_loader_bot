"""Загрузка треков Яндекс.Музыки напрямую через её API.

В октябре 2026 Яндекс.Музыка переехала на новый фронтенд: старые
`/handlers/track.jsx` и `/api/v2.1/handlers/...` отвечают 301 на главную, из-за
чего экстрактор `yandexmusic` в yt-dlp (2026.08.19) падает с
`TypeError: 'NoneType' object is not subscriptable`. Новый API доступен и по
`api.music.yandex.ru`, поэтому cookies из `cookies.txt` (домен `.yandex.ru`)
уходят вместе с запросом и аккаунт остаётся авторизованным.

Цепочка повторяет то, что делает веб-плеер: JSON трека → `download-info` →
XML с `host/path/ts/s` → подписанная ссылка `get-mp3`. Если API отдал только
30-секундное превью (нет входа или Яндекс Плюса), загрузка считается неудачной:
лучше честная ошибка, чем обрезанный файл в медиатеке.

Модуль сознательно не тянет OAuth-токен: текущей сессии в cookies достаточно,
а токен пришлось бы хранить отдельным секретом.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.cookiejar
import json
import logging
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from tune_loader_bot.config import Settings
from tune_loader_bot.media.ytdlp import MediaResult

LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.music.yandex.ru"
API_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
CHUNK_SIZE = 1024 * 1024

#: Секрет, которым Яндекс.Музыка подписывает ссылку на mp3 (так же делает yt-dlp).
_DOWNLOAD_SECRET = "XGRlBW9FXlekgbPrRHuSiA"

#: `/album/123/track/456`, `/track/456` и `/tracks/456`.
_TRACK_PATH = re.compile(r"^/(?:album/\d+/)?tracks?/(?P<track>\d+)/?$")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "X-Yandex-Music-Client": "YandexMusicWebSite",
    "Origin": "https://music.yandex.ru",
    "Referer": "https://music.yandex.ru/",
}


class YandexError(RuntimeError):
    """Ожидаемая ошибка загрузки: нет cookies, превью, недоступный трек."""


def parse_track_id(url: str) -> str | None:
    """Достаёт id трека из ссылки; `None` — ссылка не на трек."""
    path = urllib.parse.urlsplit(url.strip()).path
    match = _TRACK_PATH.match(path)
    return match.group("track") if match else None


def _load_opener(settings: Settings) -> urllib.request.OpenerDirector:
    """Opener с cookies из `cookies-file` (нужна авторизация Яндекса)."""
    cookies = Path(settings.cookies_file)
    if not cookies.is_file():
        raise YandexError(f"файл cookies не найден: {cookies}")
    jar = http.cookiejar.MozillaCookieJar(str(cookies))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except OSError as exc:
        raise YandexError(f"не удалось прочитать cookies: {cookies}: {exc}") from exc
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _get(
    opener: Any, url: str, *, timeout: int = API_TIMEOUT, api: bool = True
) -> bytes:
    headers = _HEADERS if api else {"User-Agent": _HEADERS["User-Agent"]}
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=timeout) as response:
        return response.read()


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001 - тело ошибки не должно скрывать исходную
        return ""
    return f": {body[:200]}" if body else ""


def _api_json(opener: Any, path: str) -> Any:
    url = f"{API_BASE}{path}"
    try:
        payload = _get(opener, url)
    except urllib.error.HTTPError as exc:
        raise YandexError(f"{url}: HTTP {exc.code}{_error_detail(exc)}") from exc
    except OSError as exc:
        raise YandexError(f"{url}: {exc}") from exc

    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise YandexError(f"{url}: ответ не является JSON") from exc
    if isinstance(data, dict) and data.get("error"):
        raise YandexError(f"{url}: {data['error']}")
    return data


def _fetch_track(opener: Any, track_id: str) -> dict[str, Any]:
    data = _api_json(opener, f"/tracks/{track_id}")
    result = data.get("result") if isinstance(data, dict) else None
    if isinstance(result, list):
        track = result[0] if result else None
    elif isinstance(result, dict):
        track = result
    else:
        track = None
    if not isinstance(track, dict):
        raise YandexError("API не вернул данные трека")
    return track


def _fetch_download_infos(opener: Any, track_id: str) -> list[dict[str, Any]]:
    data = _api_json(opener, f"/tracks/{track_id}/download-info")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, list) or not result:
        raise YandexError("API не вернул варианты загрузки")
    infos = [item for item in result if isinstance(item, dict)]
    if not infos:
        raise YandexError("API не вернул варианты загрузки")
    return infos


def _bitrate(info: dict[str, Any]) -> int:
    try:
        return int(info.get("bitrateInKbps") or 0)
    except (TypeError, ValueError):
        return 0


def select_download_info(
    infos: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Лучший полный mp3; `None` — если доступно только превью."""
    full = [
        info
        for info in infos
        if info.get("downloadInfoUrl") and not info.get("preview")
    ]
    if not full:
        return None
    mp3 = [info for info in full if str(info.get("codec") or "").lower() == "mp3"]
    return max(mp3 or full, key=_bitrate)


def build_download_url(opener: Any, info: dict[str, Any], track_id: str) -> str:
    """Строит подписанную ссылку на mp3 по XML из `download-info`."""
    info_url = str(info.get("downloadInfoUrl") or "")
    if not info_url.startswith("https://"):
        raise YandexError("некорректная ссылка на download-info")
    try:
        payload = _get(opener, info_url, api=False)
        root = ET.fromstring(payload)
    except (OSError, ET.ParseError) as exc:
        raise YandexError(f"не удалось разобрать download-info: {exc}") from exc

    fields = {child.tag: (child.text or "") for child in root}
    path = fields.get("path", "")
    secret = fields.get("s", "")
    ts = fields.get("ts", "")
    host = fields.get("host", "")
    if not (path and secret and ts and host):
        raise YandexError("в download-info нет host/path/ts/s")

    path_part = path[1:] if path.startswith("/") else path
    key = hashlib.md5((_DOWNLOAD_SECRET + path_part + secret).encode()).hexdigest()
    return f"https://{host}/get-mp3/{key}/{ts}{path}?track-id={track_id}"


def _artists(track: dict[str, Any]) -> str | None:
    names = [
        str(artist["name"])
        for artist in track.get("artists") or []
        if isinstance(artist, dict) and artist.get("name")
    ]
    return ", ".join(names) or None


def _album(track: dict[str, Any]) -> str | None:
    albums = track.get("albums") or []
    if albums and isinstance(albums[0], dict):
        return albums[0].get("title")
    return None


def _filename(
    settings: Settings, target: Path, track: dict[str, Any], track_id: str
) -> Path:
    """Применяет `filename-template` и гарантирует расширение `.mp3`."""
    title = str(track.get("title") or track_id)
    info = {
        "id": str(track.get("id") or track_id),
        "title": title,
        "ext": "mp3",
        "artist": _artists(track),
        "album": _album(track),
        "track": title,
    }
    template = settings.filename_template or "%(title).100s"
    try:
        with YoutubeDL(
            {"outtmpl": template, "outtmpl_na_placeholder": "", "quiet": True, "no_warnings": True}
        ) as ydl:
            name = ydl.prepare_filename(info)
    except Exception:  # noqa: BLE001 - шаблон приходит из настроек
        LOGGER.exception("не удалось применить filename-template, беру title")
        name = title

    path = Path(name) if name and name != "-" else Path(title)
    if path.suffix.lower() != ".mp3":
        path = path.with_name(f"{path.name}.mp3")

    base = target.resolve()
    candidate = (base / path).resolve()
    if candidate == base or base not in candidate.parents:
        LOGGER.warning(
            "filename-template выводит за пределы каталога загрузки, беру id трека"
        )
        candidate = base / f"{info['id']}.mp3"
    return candidate


def _download_file(opener: Any, url: str, path: Path) -> None:
    """Качает mp3 во временный `.part` и атомарно переименовывает."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.part")
    request = urllib.request.Request(url, headers={"User-Agent": _HEADERS["User-Agent"]})
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response, partial.open(
            "wb"
        ) as file:
            shutil.copyfileobj(response, file, length=CHUNK_SIZE)
        if partial.stat().st_size == 0:
            raise YandexError("Яндекс.Музыка вернула пустой файл")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _download_sync(
    settings: Settings, target: Path, track_id: str, opener: Any
) -> MediaResult:
    track = _fetch_track(opener, track_id)
    if not track.get("available", True):
        raise YandexError("трек недоступен")

    infos = _fetch_download_infos(opener, track_id)
    best = select_download_info(infos)
    if best is None:
        raise YandexError(
            "доступно только превью (30 секунд): проверьте, что cookies.txt "
            "принадлежат аккаунту с Яндекс Плюсом"
        )

    url = build_download_url(opener, best, track_id)
    path = _filename(settings, target, track, track_id)
    LOGGER.info(
        "Яндекс.Музыка: %s (%s кбит/с)", track.get("title") or track_id, _bitrate(best)
    )
    _download_file(opener, url, path)
    size = path.stat().st_size
    LOGGER.info("Яндекс.Музыка: сохранено %s (%s байт)", path.name, size)
    return MediaResult(
        file_name=path.name,
        file_path=str(path),
        file_size=size,
        title=str(track.get("title") or track_id),
        artist=_artists(track),
        channel=None,
    )


async def download_media(
    settings: Settings,
    target: str | os.PathLike[str],
    url: str,
    *,
    opener: Any | None = None,
) -> MediaResult | None:
    """Скачивает трек. `opener` инъектируется тестами (сети в тестах нет)."""
    track_id = parse_track_id(url)
    if track_id is None:
        LOGGER.error("Яндекс.Музыка: не удалось найти id трека в ссылке %s", url)
        return None

    target_dir = Path(target)
    target_dir.mkdir(parents=True, exist_ok=True)
    if opener is None:
        try:
            opener = _load_opener(settings)
        except YandexError as exc:
            LOGGER.error("Яндекс.Музыка: %s", exc)
            return None

    LOGGER.info("загрузка: %s", url)
    try:
        return await asyncio.to_thread(
            _download_sync, settings, target_dir, track_id, opener
        )
    except YandexError as exc:
        LOGGER.error("Яндекс.Музыка: %s (%s)", exc, url)
    except OSError as exc:
        LOGGER.error("Яндекс.Музыка: ошибка файловой системы при загрузке %s: %s", url, exc)
    except Exception:
        LOGGER.exception("Яндекс.Музыка: неожиданная ошибка при загрузке %s", url)
    return None
