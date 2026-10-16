"""Доменные модели: загрузка и подписка.

Модели не знают ни про SQL, ни про файловую систему: они описывают данные и
умеют превращаться в строку БД (`as_row`) и в «старый» dict
(`to_legacy_dict`) — второй нужен, пока `bot.py`/`watchdog` читают dict'ы.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"

#: Порядок важен: сначала с микросекундами (как пишет старый код), потом без.
_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    DATE_FORMAT,
)


def parse_timestamp(value: Any) -> datetime | None:
    """Терпимый разбор метки времени из SQLite.

    D12: раньше использовался строгий `"%Y-%m-%d %H:%M:%S.%f"`, поэтому строка
    без микросекунд (например, записанная другим инструментом) валила весь тик
    watchdog. Теперь непонятное значение даёт `None`, а не исключение.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_date(value: Any) -> date | None:
    """Дата из БД (`date` в downloads) — без времени."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    parsed = parse_timestamp(value)
    return parsed.date() if parsed else None


def format_timestamp(value: datetime | date | None) -> str | None:
    """Значение для колонки DATETIME: ISO-строка или None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime(TIMESTAMP_FORMAT)
    return value.strftime(DATE_FORMAT)


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class Download:
    """Запись о скачанном файле.

    `user_id = 0` — «служебный» пользователь для постов канала: раньше там был
    NULL, а NULL в SQLite не участвует в UNIQUE-индексе (D2), поэтому повторная
    запись создавала дубликаты.
    """

    url: str
    video: bool = False
    user_id: int = 0
    date: datetime | None = None
    file_name: str | None = None
    file_size: int | None = None
    title: str | None = None
    artist: str | None = None
    channel: str | None = None
    delivered: bool = False

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Download:
        return cls(
            url=row["url"],
            video=bool(row["video"]),
            user_id=_as_int(row["user_id"]),
            date=parse_timestamp(row["date"]),
            file_name=row["file_name"],
            file_size=_as_optional_int(row["file_size"]),
            title=row["title"],
            artist=row["artist"],
            channel=row["channel"],
            delivered=bool(row["delivered"]),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Download:
        """Строит модель из dict'а (в т.ч. неполного — как раньше `save_download`)."""
        return cls(
            url=data.get("url") or "",
            video=bool(data.get("video")),
            user_id=_as_int(data.get("user_id")),
            date=parse_timestamp(data.get("date")),
            file_name=data.get("file_name"),
            file_size=_as_optional_int(data.get("file_size")),
            title=data.get("title"),
            artist=data.get("artist"),
            channel=data.get("channel"),
            delivered=bool(data.get("delivered")),
        )

    @classmethod
    def coerce(cls, value: Download | Mapping[str, Any]) -> Download:
        return value if isinstance(value, Download) else cls.from_mapping(value)

    def as_row(self) -> dict[str, Any]:
        """Параметры для INSERT/UPDATE."""
        return {
            "date": format_timestamp(self.date),
            "user_id": self.user_id,
            "url": self.url,
            "video": int(self.video),
            "file_name": self.file_name,
            "file_size": self.file_size,
            "title": self.title,
            "artist": self.artist,
            "channel": self.channel,
            "delivered": int(self.delivered),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """Формат, который отдавал старый `database.find_url` (dict)."""
        return {
            "date": self.date.date() if isinstance(self.date, datetime) else self.date,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "title": self.title,
            "artist": self.artist,
            "channel": self.channel,
            "delivered": int(self.delivered),
            "url": self.url,
            "video": int(self.video),
            "user_id": self.user_id,
        }


@dataclass(slots=True)
class Subscription:
    """Подписка watchdog'а на плейлист/канал."""

    url: str
    user_id: int = 0
    keywords: str | None = None
    interval: int = 3600
    last_check: datetime | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Subscription:
        return cls(
            id=_as_optional_int(row["id"]),
            user_id=_as_int(row["user_id"]),
            url=row["url"],
            keywords=row["keywords"],
            interval=_as_int(row["interval"], default=3600),
            last_check=parse_timestamp(row["last_check"]),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Subscription:
        return cls(
            id=_as_optional_int(data.get("id")),
            user_id=_as_int(data.get("user_id")),
            url=data.get("url") or "",
            keywords=data.get("keywords"),
            interval=_as_int(data.get("interval"), default=3600),
            last_check=parse_timestamp(data.get("last_check")),
        )

    @classmethod
    def coerce(cls, value: Subscription | Mapping[str, Any]) -> Subscription:
        return value if isinstance(value, Subscription) else cls.from_mapping(value)

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "keywords": self.keywords,
            "interval": self.interval,
            "last_check": format_timestamp(self.last_check),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """Формат, который отдавал старый `database.get_subscriptions`."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "keywords": self.keywords,
            "interval": self.interval,
            "last_check": self.last_check,
        }
