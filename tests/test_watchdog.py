"""Тесты watchdog подписок (фаза 6, D13).

Проход проверяется через `tick()` — без реального `sleep`. Часы, загрузчик
плейлиста и «сон» инъектируются.
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from fakes import FakeBot, make_downloader

from tune_loader_bot.domain.models import Subscription
from tune_loader_bot.jobs.watchdog import SubscriptionWatcher, build_matcher, select_urls
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.telegram.delivery import Delivery

NOW = datetime(2026, 10, 16, 12, 0)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Loader:
    """Поддельный загрузчик плейлиста."""

    def __init__(self, playlist=None, error=None, errors=None) -> None:
        self.playlist = playlist or []
        self.error = error
        self.errors = errors or {}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, url: str, items: str):
        self.calls.append((url, items))
        if url in self.errors:
            raise self.errors[url]
        if self.error is not None:
            raise self.error
        return self.playlist


@pytest.fixture
def make_watcher(settings, storage):
    def build(*, playlist=None, error=None, errors=None, downloader=None, clock=None, bot=None, sleep=asyncio.sleep):
        library = MediaLibrary(settings, storage, downloader=downloader or make_downloader())
        loader = Loader(playlist, error, errors)
        watcher = SubscriptionWatcher(
            bot=bot or FakeBot(),
            library=library,
            delivery=Delivery(settings, library),
            settings=settings,
            playlist_loader=loader,
            clock=clock or Clock(),
            sleep=sleep,
        )
        return watcher, loader, library

    return build


# --- фильтр ключевых слов ---------------------------------------------------


def test_matcher_without_keywords_accepts_everything():
    assert build_matcher(None)("что угодно")
    assert build_matcher("")("что угодно")
    assert build_matcher("  ,  ")("что угодно")


@pytest.mark.parametrize(
    ("keywords", "title", "expected"),
    [
        ("Mix", "Morning MIX 2026", True),  # D13: раньше регистр учитывался
        ("mix", "Morning MIX 2026", True),
        ("mix", "полка", False),
        ("mix, chill", "chill out", True),
        (" mix , chill ", "MIX", True),
        ("mix, -live", "MIX live session", False),  # исключение
        ("mix, -live", "MIX studio", True),
        ("-live", "любой трек", True),
        ("-live", "LIVE concert", False),
    ],
)
def test_matcher(keywords, title, expected):
    assert build_matcher(keywords)(title) is expected


def test_select_urls_filters_and_reverses():
    playlist = [
        {"title": "new mix", "url": "https://youtu.be/new"},
        {"title": "old mix", "url": "https://youtu.be/old"},
        {"title": "другое", "url": "https://youtu.be/other"},
    ]

    assert select_urls("mix", playlist) == ["https://youtu.be/old", "https://youtu.be/new"]


def test_select_urls_skips_broken_entries():
    playlist = [None, "junk", {"title": "нет ссылки"}, {"url": "https://youtu.be/ok"}]

    assert select_urls(None, playlist) == ["https://youtu.be/ok"]


# --- расписание -------------------------------------------------------------


def test_is_due(make_watcher):
    watcher, _, _ = make_watcher()
    now = NOW

    assert watcher.is_due(Subscription(url="u", interval=3600), now) is True
    assert watcher.is_due(
        Subscription(url="u", interval=3600, last_check=now - timedelta(seconds=3601)), now
    )
    assert not watcher.is_due(
        Subscription(url="u", interval=3600, last_check=now - timedelta(seconds=3600)), now
    )
    assert not watcher.is_due(
        Subscription(url="u", interval=3600, last_check=now - timedelta(seconds=10)), now
    )


# --- проход -----------------------------------------------------------------


async def test_not_due_subscription_is_skipped(make_watcher, storage):
    watcher, loader, _ = make_watcher()
    await storage.save_subscription(
        Subscription(url="https://youtube.com/@a/videos", user_id=111, interval=3600, last_check=NOW)
    )

    await watcher.tick()

    assert loader.calls == []


async def test_new_item_is_downloaded_and_delivered(make_watcher, storage, settings):
    playlist = [{"title": "new mix", "url": "https://youtu.be/new"}]
    watcher, loader, library = make_watcher(playlist=playlist)
    await storage.save_subscription(
        Subscription(url="https://youtube.com/@a/videos", user_id=111, interval=3600)
    )

    await watcher.tick()

    assert loader.calls == [("https://youtube.com/@a/videos", settings.watch_playlist_items)]
    assert watcher._bot.audio, "файл должен быть отправлен"
    saved = await storage.find_download("https://youtu.be/new", False, 111)
    assert saved is not None and saved.delivered is True

    subscriptions = await storage.subscriptions()
    assert subscriptions[0].last_check == NOW, "last_check двигается после успешной проверки"


async def test_undelivered_item_is_resent_without_downloading(make_watcher, storage):
    downloader = make_downloader()
    watcher, loader, library = make_watcher(downloader=downloader, playlist=[])
    # запись уже есть в БД (например, канал скачал её, но не доставил)
    record = await library.download("https://youtu.be/old", False, 111)
    record["delivered"] = 0
    await library.storage.save_download(record)
    loader.playlist = [{"title": "old", "url": "https://youtu.be/old"}]
    await storage.save_subscription(
        Subscription(url="https://youtube.com/@a/videos", user_id=111, interval=3600)
    )
    downloader.calls.clear()

    await watcher.tick()

    assert downloader.calls == [], "готовый файл не должен качаться заново"
    assert watcher._bot.audio, "недоставленная запись должна быть отправлена"
    saved = await storage.find_download("https://youtu.be/old", False, 111)
    assert saved.delivered is True


async def test_delivered_item_is_not_resent(make_watcher, storage):
    watcher, loader, library = make_watcher(playlist=[])
    record = await library.download("https://youtu.be/old", False, 111)
    await library.mark_delivered(record)
    loader.playlist = [{"title": "old", "url": "https://youtu.be/old"}]
    await storage.save_subscription(
        Subscription(url="https://youtube.com/@a/videos", user_id=111, interval=3600)
    )

    await watcher.tick()

    assert watcher._bot.audio == []


async def test_keywords_filter_applies_during_tick(make_watcher, storage):
    playlist = [
        {"title": "нужный mix", "url": "https://youtu.be/mix"},
        {"title": "другое видео", "url": "https://youtu.be/other"},
    ]
    watcher, _, library = make_watcher(playlist=playlist)
    await storage.save_subscription(
        Subscription(url="https://youtube.com/@a/videos", user_id=111, keywords="mix", interval=3600)
    )

    await watcher.tick()

    assert await storage.find_download("https://youtu.be/mix", False, 111) is not None
    assert await storage.find_download("https://youtu.be/other", False, 111) is None


async def test_error_in_one_subscription_does_not_block_others(make_watcher, storage, caplog):
    """D13: раньше ошибка выкидывала из цикла и вторая подписка не проверялась."""
    first = "https://youtube.com/@broken/videos"
    second = "https://youtube.com/@fine/videos"
    watcher, loader, _ = make_watcher(
        playlist=[{"title": "track", "url": "https://youtu.be/track"}],
        errors={first: RuntimeError("плейлист недоступен")},
    )
    await storage.save_subscription(Subscription(url=first, user_id=111, interval=3600))
    await storage.save_subscription(Subscription(url=second, user_id=222, interval=3600))

    with caplog.at_level("ERROR"):
        await watcher.tick()

    assert [call[0] for call in loader.calls] == [first, second]
    assert "не удалась" in caplog.text

    subscriptions = {sub.url: sub for sub in await storage.subscriptions()}
    assert subscriptions[first].last_check is None, "при ошибке время проверки не двигается"
    assert subscriptions[second].last_check == NOW
    assert await storage.find_download("https://youtu.be/track", False, 222) is not None


async def test_download_failure_does_not_crash_tick(make_watcher, storage):
    watcher, _, _ = make_watcher(
        playlist=[{"title": "track", "url": "https://youtu.be/track"}],
        downloader=make_downloader(fail=True),
    )
    await storage.save_subscription(Subscription(url="https://youtube.com/@a/videos", user_id=111))

    await watcher.tick()

    assert watcher._bot.audio == []
    subscriptions = await storage.subscriptions()
    assert subscriptions[0].last_check == NOW


# --- жизненный цикл ---------------------------------------------------------


async def test_loop_survives_playlist_errors(make_watcher, storage):
    """Ошибка загрузки плейлиста не должна убивать фоновую задачу."""
    ticks: list[float] = []

    async def sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) >= 2:
            raise asyncio.CancelledError

    watcher, _, _ = make_watcher(
        playlist=[{"title": "track", "url": "https://youtu.be/track"}],
        error=RuntimeError("yt-dlp недоступен"),
        sleep=sleep,
    )
    await storage.save_subscription(Subscription(url="https://youtube.com/@a/videos", user_id=111))

    with pytest.raises(asyncio.CancelledError):
        await watcher.run()

    assert len(ticks) == 2, "цикл должен пережить ошибку и дойти до второго сна"


async def test_start_and_stop_manage_background_task(make_watcher):
    async def parking_sleep(seconds: float) -> None:
        await asyncio.Event().wait()  # «спим», пока задачу не отменят

    watcher, _, _ = make_watcher(sleep=parking_sleep)

    task = watcher.start()
    assert watcher.start() is task, "повторный start не создаёт вторую задачу"

    await asyncio.sleep(0)
    await watcher.stop()

    assert task.cancelled() or task.done()
    assert watcher._task is None

    await watcher.stop()  # повторная остановка безопасна
