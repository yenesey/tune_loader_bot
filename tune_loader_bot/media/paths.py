"""Раскладка файлов на диске и публичные ссылки.

Правило `{download-dir}/{YYYY}/{YYYY-MM}/{file_name}` — контракт с веб-сервером
и с уже скачанными ~93 ГБ, поэтому живёт в одном месте (раньше дублировалось в
`bot.py`).

Изменения относительно старого кода:

* `enrich_info` не мутирует входной словарь (D20) — раньше он дописывал поля
  прямо в переданный dict, из-за чего «обогащение» одного места меняло данные
  другого;
* неполные данные не роняют обработчик `KeyError`-ом, а логируются и дают `None`;
* `quote(..., safe="")` — имя файла попадает в URL целиком закодированным
  (для basename поведение то же, что и раньше).
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from tune_loader_bot.config import get_settings

LOGGER = logging.getLogger(__name__)


def sub_dir(on_date: datetime | date) -> str:
    """Относительный каталог `YYYY/YYYY-MM`."""
    return os.path.join(str(on_date.year), f"{on_date.year}-{str(on_date.month).zfill(2)}")


def target_dir(download_dir: str | os.PathLike[str], on_date: datetime | date) -> Path:
    """Каталог, в который складывается файл за дату `on_date`."""
    return Path(download_dir) / sub_dir(on_date)


def file_path(
    download_dir: str | os.PathLike[str], on_date: datetime | date, file_name: str
) -> str:
    return str(target_dir(download_dir, on_date) / file_name)


def public_url(server_root_url: str, on_date: datetime | date, file_name: str) -> str:
    """Публичная ссылка на файл (каталог раздаётся веб-сервером)."""
    quoted = urllib.parse.quote(file_name, safe="")
    return f"{server_root_url.rstrip('/')}/{sub_dir(on_date)}/{quoted}"


def enrich_info(
    info: Mapping[str, Any] | None,
    *,
    download_dir: str | None = None,
    server_root_url: str | None = None,
) -> dict[str, Any] | None:
    """Дополняет запись о загрузке производными полями.

    Возвращает **новый** dict (вход не меняется) с полями `performer`,
    `link_text`, `server_url` и, если его не было, `file_path`. Если не хватает
    `date`/`file_name` — `None` и запись в лог.
    """
    if not isinstance(info, Mapping) or not info:
        return None

    if download_dir is None or server_root_url is None:
        settings = get_settings()
        download_dir = download_dir if download_dir is not None else settings.download_dir
        server_root_url = (
            server_root_url if server_root_url is not None else settings.server_root_url
        )

    on_date = info.get("date")
    file_name = info.get("file_name")
    if on_date is None or not file_name:
        LOGGER.warning("enrich_info: нет date/file_name, поля: %s", sorted(info))
        return None

    performer = info.get("artist") or info.get("channel")
    title = info.get("title") or ""

    enriched = dict(info)
    enriched["performer"] = performer
    enriched["link_text"] = f"{performer} - {title}" if performer else title
    enriched["server_url"] = public_url(server_root_url, on_date, file_name)
    if not enriched.get("file_path"):
        enriched["file_path"] = file_path(download_dir, on_date, file_name)
    return enriched


def exists(info: Mapping[str, Any] | None) -> bool:
    """Есть ли описанный файл на диске (каталог 93 ГБ чистят вручную)."""
    if not isinstance(info, Mapping):
        return False
    path = info.get("file_path")
    return bool(path) and Path(str(path)).is_file()
