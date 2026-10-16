"""Характеризационные тесты слоя БД (D1, D2, D7, D8, D12).

Работают только с временной базой в `tmp_path`: живая `downloads.db` в тестах
не открывается вообще. Тесты фиксируют текущее поведение, включая известные
дефекты; после фазы 2 соответствующие проверки меняются на «правильное»
поведение.
"""

import sqlite3
from datetime import date, datetime

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


async def columns(conn, table: str) -> list[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in await cursor.fetchall()]


async def count(conn, table: str) -> int:
    cursor = await conn.execute(f"SELECT COUNT(*) FROM {table}")
    return (await cursor.fetchone())[0]


async def insert_subscription(conn, id_: int, last_check):
    await conn.execute(
        "INSERT INTO subscriptions(id, user_id, url, keywords, interval, last_check) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        [id_, 111, "https://www.youtube.com/playlist?list=X", "kwd", 3600, last_check],
    )
    await conn.commit()


# --- D1: схема, создаваемая кодом, не совпадает с живой ---------------------


async def test_fresh_schema_lacks_metadata_columns_D1(db):
    """D1: в `CREATE TABLE` нет `artist`/`channel`/`delivered` и учёта миграций."""
    columns_now = await columns(db._conn, "downloads")

    assert "artist" not in columns_now
    assert "channel" not in columns_now
    assert "delivered" not in columns_now

    cursor = await db._conn.execute("PRAGMA user_version")
    assert (await cursor.fetchone())[0] == 0


async def test_save_download_fails_on_fresh_schema_D1(db):
    """D1: на чистой базе запись падает — колонок, которые пишет код, нет."""
    with pytest.raises(sqlite3.OperationalError):
        await db.save_download(make_row())


# --- круговорот и конфликты (на схеме, как в живой БД) ----------------------


async def test_save_and_find_roundtrip(db_live_schema):
    await db_live_schema.save_download(make_row())

    found = await db_live_schema.find_url("https://example.com/track", False, 111)

    assert found["title"] == "Track"
    assert found["file_name"] == "track.mp3"
    assert found["file_size"] == 1234
    assert found["artist"] == "Artist"
    assert found["channel"] is None
    assert found["delivered"] == 0
    assert found["date"] == date(2026, 10, 16)


async def test_conflict_updates_only_delivered_D8(db_live_schema):
    """D8: повторная запись обновляет только `delivered`, метаданные остаются."""
    await db_live_schema.save_download(make_row())
    await db_live_schema.save_download(make_row(file_size=999, title="New title", delivered=1))

    found = await db_live_schema.find_url("https://example.com/track", False, 111)

    assert await count(db_live_schema._conn, "downloads") == 1
    assert found["delivered"] == 1
    assert found["file_size"] == 1234, "размер не обновился — это D8"
    assert found["title"] == "Track", "заголовок не обновился — это D8"


async def test_find_url_without_user_id_ignores_user_D7(db_live_schema):
    """D7: поиск без `user_id` отдаёт произвольную (первую) строку."""
    await db_live_schema.save_download(make_row(user_id=111, delivered=1))
    await db_live_schema.save_download(make_row(user_id=222, delivered=0))

    someone = await db_live_schema.find_url("https://example.com/track", False)
    own = await db_live_schema.find_url("https://example.com/track", False, 222)

    assert someone is not None
    assert own["delivered"] == 0
    assert someone["delivered"] == 1, "без user_id возвращается чужая запись — это D7"


async def test_null_user_id_allows_duplicate_rows_D2(db_live_schema):
    """D2: `user_id IS NULL` не участвует в UNIQUE-индексе → дубли строк."""
    await db_live_schema.save_download(make_row(user_id=None))
    await db_live_schema.save_download(make_row(user_id=None))

    assert await count(db_live_schema._conn, "downloads") == 2


async def test_find_url_absent_returns_none(db_live_schema):
    assert await db_live_schema.find_url("https://example.com/absent", False, 111) is None


async def test_save_download_fills_missing_keys_with_none(db_live_schema):
    """Отсутствующие ключи дописываются как NULL — отсюда возможен file_size=None."""
    await db_live_schema.save_download(
        {"date": datetime(2026, 1, 1), "url": "https://example.com/x", "video": 0, "user_id": 111}
    )

    found = await db_live_schema.find_url("https://example.com/x", False, 111)

    assert found["file_name"] is None
    assert found["file_size"] is None
    assert found["title"] is None


# --- подписки (D12) --------------------------------------------------------


async def test_get_subscriptions_parses_microseconds(db):
    await insert_subscription(db._conn, 1, "2026-10-16 17:42:50.943199")
    await insert_subscription(db._conn, 2, None)

    subs = await db.get_subscriptions()

    assert subs[0]["last_check"] == datetime(2026, 10, 16, 17, 42, 50, 943199)
    assert subs[1]["last_check"] is None
    assert subs[0]["keywords"] == "kwd"
    assert subs[0]["interval"] == 3600


async def test_get_subscriptions_rejects_timestamp_without_microseconds_D12(db):
    """D12: строгий формат `%f` — метка времени без микросекунд валит весь тик."""
    await insert_subscription(db._conn, 1, "2026-10-16 17:42:50")

    with pytest.raises(ValueError):
        await db.get_subscriptions()


async def test_save_subscription_updates_existing(db):
    await db.save_subscription(
        {"id": 1, "user_id": 111, "url": "u", "keywords": "old", "interval": 60, "last_check": None}
    )
    await db.save_subscription(
        {"id": 1, "user_id": 111, "url": "u", "keywords": "new", "interval": 120, "last_check": None}
    )

    subs = await db.get_subscriptions()

    assert len(subs) == 1
    assert subs[0]["keywords"] == "new"
    assert subs[0]["interval"] == 120
