"""Совместимость переходного dict-API (`database.Database`).

Пока `bot.py` и watchdog не переписаны (фазы 5-6), старый код работает через
этот шим. Тесты фиксируют, что имена методов, dict-и и семантика сохранены, но
известные дефекты (D7/D8/D15) уже исправлены внутри.
"""

import importlib
from datetime import date, datetime
from pathlib import Path

import pytest


def make_row(**overrides) -> dict:
    row = {
        "date": datetime(2026, 10, 16, 12, 0),
        "user_id": 111,
        "url": "https://example.com/track",
        "video": 0,
        "file_name": "track.mp3",
        "file_size": 1234,
        "title": "Track",
        "artist": "Artist",
        "channel": None,
        "delivered": 0,
    }
    row.update(overrides)
    return row


async def test_database_file_comes_from_settings(legacy_db):
    """D15: путь к базе берётся из настроек, а не из жёсткой строки в коде."""
    storage_module = importlib.import_module("tune_loader_bot.storage.db")

    assert legacy_db._storage is not None
    assert isinstance(legacy_db._storage, storage_module.Storage)
    assert Path("downloads.db").exists()


async def test_save_and_find_url_returns_dict(legacy_db):
    await legacy_db.save_download(make_row())

    found = await legacy_db.find_url("https://example.com/track", False, 111)

    assert isinstance(found, dict)
    assert found["title"] == "Track"
    assert found["date"] == date(2026, 10, 16)
    assert found["delivered"] == 0
    for key in ("file_name", "file_size", "artist", "channel", "url", "video", "user_id"):
        assert key in found


async def test_find_url_updates_metadata_on_conflict(legacy_db):
    """D8 исправлен: повторная запись обновляет размер и заголовок."""
    await legacy_db.save_download(make_row())
    await legacy_db.save_download(make_row(file_size=999, title="New", delivered=1))

    found = await legacy_db.find_url("https://example.com/track", False, 111)

    assert found["file_size"] == 999
    assert found["title"] == "New"
    assert found["delivered"] == 1


async def test_find_url_without_user_id_returns_newest(legacy_db):
    """D7 исправлен: без user_id возвращается самая свежая запись, а не любая."""
    await legacy_db.save_download(make_row(user_id=111, date=datetime(2026, 1, 1), delivered=1))
    await legacy_db.save_download(make_row(user_id=222, date=datetime(2026, 5, 5), delivered=0))

    found = await legacy_db.find_url("https://example.com/track", False)

    assert found["user_id"] == 222
    assert found["delivered"] == 0


async def test_subscriptions_keep_dict_contract(legacy_db):
    await legacy_db.save_subscription(
        {
            "id": None,
            "user_id": 111,
            "url": "https://www.youtube.com/@channel/videos",
            "keywords": "mix",
            "interval": 600,
            "last_check": None,
        }
    )

    subscriptions = await legacy_db.get_subscriptions()

    assert len(subscriptions) == 1
    assert subscriptions[0]["keywords"] == "mix"
    assert subscriptions[0]["interval"] == 600
    assert subscriptions[0]["last_check"] is None
    assert subscriptions[0]["id"] is not None


async def test_usage_before_create_raises(project):
    database = importlib.import_module("database")

    with pytest.raises(RuntimeError, match="create"):
        await database.Database().save_download({"url": "u"})


async def test_close_is_idempotent(legacy_db):
    await legacy_db.close()
    await legacy_db.close()
