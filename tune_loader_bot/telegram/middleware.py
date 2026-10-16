"""Проверка доступа: апдейты от чужих пользователей отбрасываются.

Отличия от версии в `bot.py` (D19/D25):

* список разрешённых пользователей передаётся в конструктор, а не берётся из
  глобального `SETTINGS`;
* в лог попадает только id пользователя, без дампа `data.keys()`;
* посты канала (`event_from_user` не задан — aiogram кладёт ключ только когда
  есть пользователь) проходят как раньше.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject

LOGGER = logging.getLogger(__name__)


class SecurityMiddleware(BaseMiddleware):
    """Пускает только пользователей из списка."""

    def __init__(self, allowed_users: Iterable[int]) -> None:
        self._allowed = {int(user) for user in allowed_users}

    @property
    def allowed(self) -> frozenset[int]:
        return frozenset(self._allowed)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and int(user.id) not in self._allowed:
            LOGGER.warning("апдейт от неизвестного пользователя %s отброшен", user.id)
            return None
        return await handler(event, data)
