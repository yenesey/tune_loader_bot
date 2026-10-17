"""Фоновый обход подписок (watchdog).

Раньше цикл жил прямо в `bot.py` и имел три проблемы (D13):

* `last_check` сохранялся **до** проверки — если плейлист не получен, подписка
  считалась проверенной и ждала следующего интервала;
* ошибка на первой подписке выбрасывала из цикла до следующего тика, остальные
  подписки в этот проход не проверялись;
* ключевые слова сравнивались как подстрока **с учётом регистра**, а число
  проверяемых записей плейлиста было захардкожено (`"1,2"`).

Теперь один проход — это `tick()`, который тестируется без `sleep`; `clock`,
`sleep` и загрузчик плейлиста инъектируются. `start()/stop()` дают фоновую
задачу и аккуратную остановку (нужна для graceful shutdown).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot

from tune_loader_bot.config import Settings
from tune_loader_bot.domain.models import Subscription
from tune_loader_bot.library import MediaLibrary
from tune_loader_bot.media import ytdlp
from tune_loader_bot.telegram.delivery import Delivery

LOGGER = logging.getLogger(__name__)

PlaylistLoader = Callable[[str, str], Awaitable[Sequence[dict[str, Any]]]]


def build_matcher(keywords: str | None) -> Callable[[str | None], bool]:
    """Фильтр по ключевым словам.

    Разделитель — запятая. Слово без префикса включает, слово с префиксом `-`
    исключает; сравнение без учёта регистра. Пустой список — «подходит всё».
    """
    include: list[str] = []
    exclude: list[str] = []

    for raw in (keywords or "").split(","):
        token = raw.strip()
        if not token:
            continue
        if token.startswith("-") and len(token) > 1:
            exclude.append(token[1:].strip().lower())
        else:
            include.append(token.lower())

    def matches(title: str | None) -> bool:
        text = (title or "").lower()
        if any(bad in text for bad in exclude):
            return False
        return not include or any(good in text for good in include)

    return matches


def select_urls(keywords: str | None, playlist: Sequence[dict[str, Any]]) -> list[str]:
    """Ссылки из плейлиста, подходящие под фильтр (старые — первыми, как раньше)."""
    matches = build_matcher(keywords)
    urls = [
        str(entry["url"])
        for entry in playlist
        if isinstance(entry, dict) and entry.get("url") and matches(entry.get("title"))
    ]
    urls.reverse()  # elders first
    return urls


class SubscriptionWatcher:
    """Проверяет подписки и досылает новые/недоставленные записи."""

    def __init__(
        self,
        *,
        bot: Bot,
        library: MediaLibrary,
        delivery: Delivery,
        settings: Settings,
        playlist_loader: PlaylistLoader | None = None,
        clock: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bot = bot
        self._library = library
        self._delivery = delivery
        self._settings = settings
        self._load_playlist = playlist_loader or self._default_loader
        self._clock = clock
        self._sleep = sleep
        self._task: asyncio.Task | None = None

    # --- жизненный цикл ----------------------------------------------------

    def start(self) -> asyncio.Task:
        """Запускает фоновую задачу (повторный вызов не создаёт вторую)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="subscription-watcher")
        return self._task

    async def stop(self) -> None:
        """Отменяет задачу и дожидается её завершения."""
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def run(self) -> None:
        """Бесконечный цикл (останавливается отменой задачи)."""
        while True:
            await self._sleep(self._settings.watch_tick_seconds)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("ошибка в цикле watchdog")

    # --- один проход ------------------------------------------------------

    async def tick(self) -> None:
        """Один проход по подпискам (то, что раньше было телом `while True`)."""
        now = self._clock()
        for subscription in await self._library.storage.subscriptions():
            if not self.is_due(subscription, now):
                continue
            try:
                await self._check(subscription)
            except Exception:
                LOGGER.exception("подписка %s: проверка не удалась", subscription.url)
                continue  # last_check не двигаем: повторим в следующий тик

            subscription.last_check = self._clock()
            await self._library.storage.save_subscription(subscription)

    def is_due(self, subscription: Subscription, now: datetime) -> bool:
        if subscription.last_check is None:
            return True
        return subscription.last_check + timedelta(seconds=subscription.interval) < now

    async def _check(self, subscription: Subscription) -> None:
        playlist = await self._load_playlist(
            subscription.url, self._settings.watch_playlist_items
        )
        for url in select_urls(subscription.keywords, playlist):
            record = await self._library.find(url, False, subscription.user_id)
            if record is None:
                record = await self._library.download(url, False, subscription.user_id)
                if record is None:
                    continue
                await self._delivery.send_to_chat(self._bot, subscription.user_id, record)
                continue

            if not record.get("delivered"):
                LOGGER.info("запись %s не доставлена — повторная отправка", url)
                await self._delivery.send_to_chat(self._bot, subscription.user_id, record)

    async def _default_loader(self, url: str, items: str) -> Sequence[dict[str, Any]]:
        return await ytdlp.download_playlist(self._settings, url, items=items)
