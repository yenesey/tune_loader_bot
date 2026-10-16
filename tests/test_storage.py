"""Тесты слоя хранения (`tune_loader_bot.storage`).

Фиксируют исправления D1/D2/D7/D8/D15 на временной базе: живая `downloads.db`
здесь не участвует (см. `isolated_cwd` в conftest).
"""

from datetime import datetime

import pytest

from tune_loader_bot.domain.models import Download, Subscription
from tune_loader_bot.storage import migrations

pytestmark = pytest.mark.usefixtures("storage")


def make_download(**overrides) -> Download:
    data = {
        "url": "https://example.com/track",
        "video": False,
        "user_id": 111,
        "date": datetime(2026, 10, 16, 12, 0),
        "file_name": "track.mp3",
        "file_size": 1234,
        "title": "Track",
        "artist": "Artist",
        "channel": None,
        "delivered": False,
    }
    data.update(overrides)
    return Download.from_mapping(data)


async def test_fresh_database_has_full_schema(storage):
    """D1: схема, создаваемая кодом, содержит все колонки, которые он пишет."""
    columns = await migrations.table_columns(storage.connection, "downloads")

    assert {"artist", "channel", "delivered"} <= set(columns)
    assert await migrations.get_user_version(storage.connection) == migrations.CURRENT_VERSION


async def test_database_uses_wal(storage):
    cursor = await storage.connection.execute("PRAGMA journal_mode")
    assert (await cursor.fetchone())[0].lower() == "wal"


async def test_save_and_find_roundtrip(storage):
    await storage.save_download(make_download())

    found = await storage.find_download("https://example.com/track", False, 111)

    assert found.title == "Track"
    assert found.file_name == "track.mp3"
    assert found.file_size == 1234
    assert found.artist == "Artist"
    assert found.channel is None
    assert found.delivered is False
    assert found.date == datetime(2026, 10, 16, 12, 0)


async def test_save_accepts_plain_dict(storage):
    """Переходный `database.py` передаёт dict'ы — они тоже принимаются."""
    await storage.save_download({"url": "u", "video": 0, "user_id": 111, "title": "t"})

    found = await storage.find_download("u", False, 111)

    assert found.title == "t"
    assert found.file_size is None


async def test_save_tolerates_missing_keys(storage):
    saved = await storage.save_download({"url": "u", "video": 0, "user_id": 111})

    assert saved.file_name is None
    assert await storage.count_downloads() == 1


async def test_conflict_updates_all_metadata(storage):
    """D8: повторная запись обновляет метаданные, а не только `delivered`."""
    await storage.save_download(make_download())
    await storage.save_download(make_download(file_size=999, title="New title", file_name="new.mp3"))

    found = await storage.find_download("https://example.com/track", False, 111)

    assert await storage.count_downloads() == 1
    assert found.file_size == 999
    assert found.title == "New title"
    assert found.file_name == "new.mp3"


async def test_delivered_is_never_reset(storage):
    """Повторная загрузка не должна «забывать», что файл уже доставлен."""
    await storage.save_download(make_download(delivered=True))
    await storage.save_download(make_download(delivered=False))

    found = await storage.find_download("https://example.com/track", False, 111)

    assert found.delivered is True


async def test_null_user_id_becomes_zero_and_stays_unique(storage):
    """D2: посты канала (user_id=0) больше не создают дубликаты строк."""
    await storage.save_download(make_download(user_id=None))
    await storage.save_download(make_download(user_id=None))

    assert await storage.count_downloads() == 1

    found = await storage.find_download("https://example.com/track", False, 0)
    assert found is not None
    assert found.user_id == 0


async def test_find_without_user_id_returns_newest(storage):
    """D7: поиск «для кого угодно» детерминирован — самая свежая запись."""
    await storage.save_download(make_download(user_id=111, date=datetime(2026, 1, 1), delivered=True))
    await storage.save_download(make_download(user_id=222, date=datetime(2026, 5, 5), delivered=False))

    someone = await storage.find_download("https://example.com/track", False)
    own = await storage.find_download("https://example.com/track", False, 222)

    assert someone.user_id == 222
    assert someone.date == datetime(2026, 5, 5)
    assert own.delivered is False


async def test_find_absent_returns_none(storage):
    assert await storage.find_download("https://example.com/absent", False, 111) is None


async def test_find_distinguishes_video_and_audio(storage):
    await storage.save_download(make_download(video=False, title="audio"))
    await storage.save_download(make_download(video=True, title="video"))

    audio = await storage.find_download("https://example.com/track", False, 111)
    video = await storage.find_download("https://example.com/track", True, 111)

    assert (audio.title, video.title) == ("audio", "video")
    assert await storage.count_downloads() == 2


async def test_subscriptions_roundtrip(storage):
    saved = await storage.save_subscription(
        Subscription(url="https://youtube.com/@channel/videos", user_id=111, keywords="mix", interval=600)
    )

    assert saved.id is not None
    subscriptions = await storage.subscriptions()
    assert len(subscriptions) == 1
    assert subscriptions[0].url == "https://youtube.com/@channel/videos"
    assert subscriptions[0].keywords == "mix"
    assert subscriptions[0].interval == 600
    assert subscriptions[0].last_check is None


async def test_subscription_upsert_updates_fields(storage):
    saved = await storage.save_subscription(Subscription(url="u", user_id=111, interval=60))
    saved.keywords = "new"
    saved.interval = 120
    saved.last_check = datetime(2026, 10, 16, 13, 0)
    await storage.save_subscription(saved)

    subscriptions = await storage.subscriptions()

    assert len(subscriptions) == 1
    assert subscriptions[0].keywords == "new"
    assert subscriptions[0].interval == 120
    assert subscriptions[0].last_check == datetime(2026, 10, 16, 13, 0)


async def test_save_subscriptions_bulk(storage):
    await storage.save_subscriptions(
        [Subscription(url="a", user_id=111), Subscription(url="b", user_id=222)]
    )

    assert {sub.url for sub in await storage.subscriptions()} == {"a", "b"}
