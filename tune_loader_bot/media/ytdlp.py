"""Работа с yt-dlp: параметры, загрузка одного медиа, разбор плейлиста.

Что исправлено относительно старого `download.py`:

* **D3** — не осталось глобального мутируемого `YTDL_OPTS`: `build_options`
  возвращает **новый** dict на каждый вызов, поэтому одновременные загрузки
  (пользователь + watchdog) больше не перемешивают `format`/`postprocessors`;
* **D4** — `extractor_args` собран в правильной форме
  (`{"youtube": {"player_client": ["web"], "po_token": [...]}}`); раньше
  `player_client` и `youtube_player_js_*` лежали на уровне имён экстракторов и
  молча игнорировались;
* **D6** — хук `MoveFiles` больше не падает на `filepath = None` и не зовёт
  `os.path.getsize` для несуществующего файла; путь ищется по нескольким
  кандидатам и проверяется на диске;
* **D22** — путь к Deno берётся из настроек (или `shutil.which("deno")`), путь к
  cookies — из настроек, а не из `os.getcwd()` на импорте;
* **D23** — для одиночного медиа выставляется `noplaylist`, поэтому вставленная
  ссылка на плейлист/канал не превращается в веерную загрузку;
* **D24** — разбор плейлиста терпим к `info = None` и `entries = None`;
* добавлены `retries`/`socket_timeout`/`extractor_retries` и ограничение
  одновременных загрузок (`max_concurrent_downloads`) с блокировкой по URL.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from tune_loader_bot.config import Settings
from tune_loader_bot.domain import services

LOGGER = logging.getLogger(__name__)
YTDL_LOGGER = logging.getLogger("yt-dlp")

DEFAULT_RETRIES = 3
DEFAULT_SOCKET_TIMEOUT = 30
DEFAULT_EXTRACTOR_RETRIES = 2


class YdlFactory(Protocol):
    """Фабрика `YoutubeDL` — инъекция нужна тестам (сети в тестах нет)."""

    def __call__(self, options: dict[str, Any]) -> Any: ...


@dataclass(slots=True)
class MediaResult:
    """Результат успешной загрузки одного файла."""

    file_name: str
    file_path: str
    file_size: int | None = None
    title: str | None = None
    artist: str | None = None
    channel: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Формат, который ждёт переходный `download.py`/`database.save_download`."""
        return {
            "title": self.title,
            "artist": self.artist,
            "channel": self.channel,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "file_size": self.file_size,
        }


class YdlLogAdapter:
    """Логгер для yt-dlp.

    Раньше в опции передавался модуль `logging`: yt-dlp писал свои `info` в
    INFO-уровень бота и засорял журнал. Теперь его «info» — это DEBUG.
    """

    def __init__(self, logger: logging.Logger = YTDL_LOGGER) -> None:
        self._logger = logger

    def debug(self, message: Any) -> None:
        text = str(message)
        if not text.startswith("[debug] "):
            self._logger.debug(text)

    def info(self, message: Any) -> None:
        self._logger.debug(message)

    def warning(self, message: Any) -> None:
        self._logger.warning(message)

    def error(self, message: Any) -> None:
        self._logger.error(message)


class MoveFilesHook:
    """Постпроцессорный хук: собирает путь к готовому файлу и метаданные.

    D6: старый хук падал на `filepath = None` (и на файле, которого нет), из-за
    чего результат терялся, а `file_size` оставался `None`.
    """

    def __init__(self) -> None:
        self.file_path: str | None = None
        self.title: str | None = None
        self.artist: str | None = None
        self.channel: str | None = None
        self._seen: dict[str, str] = {}

    def __call__(self, data: dict[str, Any]) -> None:
        info = data.get("info_dict") or {}
        postprocessor = str(data.get("postprocessor") or "")
        status = str(data.get("status") or "")

        if self._seen.get(postprocessor) != status:
            LOGGER.info(
                "yt-dlp: %s: %s: %s", postprocessor, status, info.get("title") or "?"
            )
        self._seen[postprocessor] = status

        self.title = info.get("title") or self.title
        self.artist = info.get("artist") or self.artist
        self.channel = info.get("channel") or info.get("uploader") or self.channel

        if postprocessor == "MoveFiles" and status == "finished":
            self.file_path = info.get("filepath") or info.get("_filename") or self.file_path


def find_deno(settings: Settings, *, home: Path | None = None) -> str | None:
    """Ищет JavaScript-рантайм Deno.

    Порядок: настройка `deno-path` → `PATH` → стандартное место установки
    `~/.deno/bin/deno`.

    Последний шаг появился после регрессии 16.10.2026: старый код хардкодил
    `/home/denis/.deno/bin/deno`, при переходе на «настройка или PATH» в
    systemd-сервисе (у него минимальный `PATH`, а `bash -c ... activate` не
    добавляет `~/.deno/bin`) Deno перестал находиться, и YouTube отвечал
    «Requested format is not available» из-за нерешённой n-challenge.
    """
    if settings.deno_path:
        return settings.deno_path

    from_path = shutil.which("deno")
    if from_path:
        return from_path

    base = Path(home) if home is not None else Path.home()
    fallback = base / ".deno" / "bin" / "deno"
    return str(fallback) if fallback.is_file() else None


def _js_runtimes(settings: Settings) -> dict[str, Any]:
    deno = find_deno(settings)
    if not deno:
        LOGGER.warning(
            "Deno не найден (искали 'deno-path', PATH и ~/.deno/bin/deno): "
            "часть форматов YouTube может не расшифровываться"
        )
        return {}
    LOGGER.debug("JavaScript-рантайм: %s", deno)
    return {"deno": {"path": deno}}


def _cookiefile(settings: Settings) -> str | None:
    cookies = Path(settings.cookies_file)
    if not cookies.is_file():
        LOGGER.warning(
            "файл cookies не найден: %s — YouTube может требовать авторизацию", cookies
        )
        return None
    return str(cookies)


def youtube_extractor_args(settings: Settings) -> dict[str, Any]:
    """Аргументы экстрактора YouTube в форме `{имя_экстрактора: {аргумент: [значения]}}`.

    Значения обязаны быть **списками**: если передать строку, yt-dlp разберёт её
    по символам (`"actual"` → `"a"`), о чём предупреждает в логе. Проверено на
    установленной версии `yt-dlp` (2026.8.19).
    """
    youtube: dict[str, Any] = {"player_client": ["web"]}
    tokens = [
        f"web.gvs+{settings.po_token_gvs}" if settings.po_token_gvs else "",
        f"web.player+{settings.po_token_web}" if settings.po_token_web else "",
    ]
    tokens = [token for token in tokens if token]
    if tokens:
        youtube["po_token"] = tokens
    else:
        LOGGER.warning("po-token'ы не заданы: часть видео YouTube может не скачиваться")
    youtube["player_js_version"] = ["actual"]
    youtube["player_js_variant"] = ["main"]
    return {"youtube": youtube}


def build_options(
    settings: Settings,
    target: str | os.PathLike[str],
    *,
    service: str | None = None,
    video: bool = False,
    for_playlist: bool = False,
    playlist_items: str | None = None,
    postprocessor_hook: Callable[[dict[str, Any]], None] | None = None,
    retries: int = DEFAULT_RETRIES,
    socket_timeout: int = DEFAULT_SOCKET_TIMEOUT,
) -> dict[str, Any]:
    """Собирает параметры yt-dlp. Всегда возвращает **новый** dict (D3)."""
    home = str(target)
    options: dict[str, Any] = {
        "paths": {"temp": home, "home": home},
        "outtmpl": settings.filename_template,
        "outtmpl_na_placeholder": "",
        "overwrites": True,
        "noprogress": True,
        "quiet": True,
        "logger": YdlLogAdapter(),
        "retries": retries,
        "fragment_retries": retries,
        "extractor_retries": DEFAULT_EXTRACTOR_RETRIES,
        "socket_timeout": socket_timeout,
        "postprocessors": [],
        "progress_hooks": [],
        "postprocessor_hooks": [postprocessor_hook] if postprocessor_hook else [],
    }

    js_runtimes = _js_runtimes(settings)
    if js_runtimes:
        options["js_runtimes"] = js_runtimes

    cookiefile = _cookiefile(settings)
    if cookiefile:
        options["cookiefile"] = cookiefile

    options["extractor_args"] = youtube_extractor_args(settings)

    if for_playlist:
        options.update(
            {
                "skip_download": True,
                "extract_flat": "in_playlist",
                "ignoreerrors": True,
                "playlist_items": playlist_items or settings.watch_playlist_items,
            }
        )
        return options

    options["noplaylist"] = True  # D23
    # Селектор формата — знание о сервисе, поэтому живёт в реестре (фаза 4).
    options["format"] = services.format_selector(service, video)
    options["postprocessors"] = (
        [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
        if video
        else [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    )
    return options


# --- ограничение параллельных загрузок ------------------------------------
#
# Глобальное состояние здесь — это *лимиты*, а не конфигурация: они не влияют
# на содержимое опций. Блокировка по URL берётся раньше семафора, иначе две
# задачи на одну ссылку могут занять все слоты и заклинить друг друга.

_semaphore: asyncio.Semaphore | None = None
_semaphore_size: int | None = None
_url_locks: dict[tuple[str, bool], asyncio.Lock] = {}


def _download_slot(settings: Settings) -> asyncio.Semaphore:
    global _semaphore, _semaphore_size
    if _semaphore is None or _semaphore_size != settings.max_concurrent_downloads:
        _semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)
        _semaphore_size = settings.max_concurrent_downloads
    return _semaphore


def _url_lock(url: str, video: bool) -> asyncio.Lock:
    return _url_locks.setdefault((url, video), asyncio.Lock())


def reset_limits() -> None:
    """Сбрасывает лимиты (тесты, смена настроек)."""
    global _semaphore, _semaphore_size
    _semaphore = None
    _semaphore_size = None
    _url_locks.clear()


def _resolve_result(
    info: Any, target: Path, hook: MoveFilesHook, url: str
) -> MediaResult | None:
    """Ищет реально существующий файл среди кандидатов, отданных yt-dlp."""
    candidates: list[Any] = [hook.file_path]
    if isinstance(info, dict):
        candidates.append(info.get("filepath"))
        candidates.append(info.get("_filename"))
        for entry in info.get("requested_downloads") or []:
            if isinstance(entry, dict):
                candidates.append(entry.get("filepath"))

    meta: dict[str, Any] = info if isinstance(info, dict) else {}
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(str(candidate))
        if not path.is_file():
            local = target / path.name
            if local.is_file():
                path = local
            else:
                continue
        return MediaResult(
            file_name=path.name,
            file_path=str(path),
            file_size=path.stat().st_size,
            title=meta.get("title") or hook.title,
            artist=meta.get("artist") or hook.artist,
            channel=meta.get("channel") or meta.get("uploader") or hook.channel,
        )

    LOGGER.error(
        "yt-dlp не вернул путь к файлу (или файла нет на диске): %s; кандидаты: %s",
        url,
        [str(c) for c in candidates if c],
    )
    return None


def _download_sync(
    options: dict[str, Any], url: str, ydl_factory: YdlFactory
) -> Any:
    with ydl_factory(options) as ydl:
        LOGGER.info("загрузка: %s", url)
        return ydl.extract_info(url, download=True)


async def download_media(
    settings: Settings,
    target: str | os.PathLike[str],
    service: str | None,
    url: str,
    video: bool = False,
    *,
    ydl_factory: YdlFactory = YoutubeDL,
) -> MediaResult | None:
    """Скачивает один трек/видео. Возвращает `None`, если не получилось."""
    target_dir = Path(target)
    target_dir.mkdir(parents=True, exist_ok=True)
    hook = MoveFilesHook()
    options = build_options(
        settings, target_dir, service=service, video=video, postprocessor_hook=hook
    )

    info: Any = None
    # Сначала блокировка по URL, потом общий семафор: обратный порядок может
    # заклинить две задачи, запросившие одну ссылку.
    async with _url_lock(url, video):
        async with _download_slot(settings):
            try:
                info = await asyncio.to_thread(_download_sync, options, url, ydl_factory)
            except DownloadError as exc:
                LOGGER.error("ошибка yt-dlp при загрузке %s: %s", url, exc)
            except OSError as exc:
                LOGGER.error("ошибка файловой системы при загрузке %s: %s", url, exc)
            except Exception:
                LOGGER.exception("неожиданная ошибка при загрузке %s", url)

    if info is None and hook.file_path is None:
        return None
    return _resolve_result(info, target_dir, hook, url)


async def download_playlist(
    settings: Settings,
    url: str,
    items: str | None = None,
    *,
    ydl_factory: YdlFactory = YoutubeDL,
) -> list[dict[str, Any]]:
    """Разбирает плейлист/канал без скачивания. Ошибки → пустой список (D24)."""
    options = build_options(settings, settings.download_dir, for_playlist=True, playlist_items=items)

    def extract() -> Any:
        with ydl_factory(options) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.to_thread(extract)
    except Exception:
        LOGGER.exception("не удалось разобрать плейлист %s", url)
        return []

    if not isinstance(info, dict):
        LOGGER.warning("плейлист %s: yt-dlp вернул %r", url, info)
        return []

    entries = info.get("entries") or []
    result = [entry for entry in entries if isinstance(entry, dict)]
    if not result:
        LOGGER.warning("плейлист %s: записей нет", url)
    return result
