"""Сборка `Dispatcher`.

Фабрика гарантирует, что в бою и в тестах используется один и тот же объект:
middleware доступа регистрируется здесь, а не под `if __name__ == "__main__"`
(D19).
"""

from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from tune_loader_bot.telegram.handlers import HandlerContext, build_router
from tune_loader_bot.telegram.middleware import SecurityMiddleware


def create_dispatcher(
    ctx: HandlerContext, *, storage: BaseStorage | None = None
) -> Dispatcher:
    """Диспетчер с middleware доступа и роутером обработчиков."""
    dispatcher = Dispatcher(storage=storage or MemoryStorage())
    dispatcher.update.outer_middleware(SecurityMiddleware(ctx.settings.users_list))
    dispatcher.include_router(build_router(ctx))
    return dispatcher
