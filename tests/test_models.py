"""Тесты доменных моделей: разбор строк БД, терпимые даты, legacy-dict."""

from datetime import date, datetime

import pytest

from tune_loader_bot.domain.models import (
    Download,
    Subscription,
    format_timestamp,
    parse_date,
    parse_timestamp,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-16 12:00:00.123456", datetime(2026, 10, 16, 12, 0, 0, 123456)),
        ("2026-10-16 12:00:00", datetime(2026, 10, 16, 12, 0, 0)),
        ("2026-10-16T12:00:00", datetime(2026, 10, 16, 12, 0, 0)),
        ("2026-10-16", datetime(2026, 10, 16)),
        (datetime(2026, 1, 2, 3, 4, 5), datetime(2026, 1, 2, 3, 4, 5)),
        (None, None),
        ("", None),
        ("не дата", None),
        ("2026-13-45 99:00:00", None),
    ],
)
def test_parse_timestamp_is_tolerant(value, expected):
    """D12: раньше строгий `%f` ронял разбор метки времени без микросекунд."""
    assert parse_timestamp(value) == expected


def test_parse_date_truncates_time():
    assert parse_date("2026-10-16 12:34:56.789") == date(2026, 10, 16)
    assert parse_date(date(2026, 10, 16)) == date(2026, 10, 16)
    assert parse_date(None) is None


def test_format_timestamp():
    assert format_timestamp(datetime(2026, 10, 16, 12, 0, 0, 123456)) == "2026-10-16 12:00:00"
    assert format_timestamp(date(2026, 10, 16)) == "2026-10-16"
    assert format_timestamp(None) is None


def test_download_from_mapping_fills_defaults():
    download = Download.from_mapping({"url": "https://example.com/x"})

    assert download.url == "https://example.com/x"
    assert download.video is False
    assert download.user_id == 0  # NULL/отсутствие user_id больше не возможно (D2)
    assert download.delivered is False
    assert download.file_size is None


@pytest.mark.parametrize(("value", "expected"), [(None, 0), ("", 0), ("123", 123), (123, 123), ("abc", 0)])
def test_download_user_id_is_always_int(value, expected):
    assert Download.from_mapping({"url": "u", "user_id": value}).user_id == expected


def test_download_as_row_is_sql_ready():
    download = Download(
        url="u",
        video=True,
        user_id=111,
        date=datetime(2026, 10, 16, 12, 0, 0, 500000),
        file_name="f.mp3",
        file_size=10,
        title="t",
        artist="a",
        channel="c",
        delivered=True,
    )

    row = download.as_row()

    assert row["video"] == 1
    assert row["delivered"] == 1
    assert row["date"] == "2026-10-16 12:00:00"
    assert set(row) == {
        "date", "user_id", "url", "video", "file_name",
        "file_size", "title", "artist", "channel", "delivered",
    }


def test_download_to_legacy_dict_matches_old_find_url():
    download = Download.from_mapping(
        {
            "url": "u",
            "video": 1,
            "user_id": 111,
            "date": datetime(2026, 10, 16, 12, 0),
            "file_name": "f.mp3",
            "file_size": 10,
            "title": "t",
            "artist": "a",
            "channel": None,
            "delivered": 1,
        }
    )

    legacy = download.to_legacy_dict()

    # старый find_url отдавал дату без времени
    assert legacy["date"] == date(2026, 10, 16)
    assert legacy["delivered"] == 1
    assert legacy["channel"] is None
    assert set(legacy) == {
        "date", "file_name", "file_size", "title", "artist", "channel", "delivered",
        "url", "video", "user_id",
    }


def test_subscription_from_mapping_defaults_interval():
    subscription = Subscription.from_mapping({"url": "u", "interval": None})

    assert subscription.interval == 3600
    assert subscription.last_check is None
    assert subscription.id is None


def test_subscription_as_row_and_legacy():
    subscription = Subscription(
        id=2, user_id=111, url="u", keywords="k", interval=60, last_check=datetime(2026, 1, 1, 10, 0)
    )

    assert subscription.as_row()["last_check"] == "2026-01-01 10:00:00"
    assert subscription.to_legacy_dict()["last_check"] == datetime(2026, 1, 1, 10, 0)
