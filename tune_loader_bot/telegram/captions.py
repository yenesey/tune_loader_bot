"""Подписи и ссылки в сообщениях.

Раньше одни и те же выражения `hlink("#origin", url) + "  " + hlink("#file", …)`
собирались в двух местах (`process_message` и `send_audio_message`, D21) и могли
разъехаться. Теперь это одна пара функций.
"""

from __future__ import annotations

from typing import Any, Mapping

from aiogram.utils.markdown import hlink


def origin_link(url: str) -> str:
    """Ссылка на исходное сообщение."""
    return hlink("#origin", url)


def file_link(record: Mapping[str, Any]) -> str:
    """Ссылка на файл в веб-каталоге."""
    return hlink("#file", record["server_url"])


def media_caption(record: Mapping[str, Any]) -> str:
    """Подпись к отправляемому аудио/видео."""
    return f"{origin_link(record['url'])}  {file_link(record)}"


def link_message(record: Mapping[str, Any]) -> str:
    """Текст, когда файл слишком большой для Telegram."""
    return f"{hlink(record['link_text'], record['server_url'])}\n{origin_link(record['url'])}"
