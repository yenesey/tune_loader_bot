"""Клавиатура диалога выбора формата.

Набор кнопок берётся из реестра сервисов (`domain.services`), а не из локального
словаря со строками `("audio")` (D10).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tune_loader_bot.domain import services
from tune_loader_bot.telegram import texts

BUTTONS: dict[str, tuple[str, str]] = {
    services.AUDIO: (texts.BUTTON_AUDIO, services.AUDIO),
    services.VIDEO: (texts.BUTTON_VIDEO, services.VIDEO),
}

EXIT_BUTTON: tuple[str, str] = (texts.BUTTON_EXIT, "exit")


def download_dialog(service_name: str) -> InlineKeyboardMarkup:
    """Клавиатура для сервиса. `KeyError` — если сервиса нет в реестре."""
    available = services.formats(service_name)
    entries = [BUTTONS[fmt] for fmt in available] + [EXIT_BUTTON]

    builder = InlineKeyboardBuilder()
    builder.row(
        *(InlineKeyboardButton(text=text, callback_data=data) for text, data in entries)
    )
    return builder.as_markup()


def download_dialog_message(service_name: str) -> dict[str, object]:
    """Готовые аргументы для `Message.answer(...)`."""
    return {"text": texts.DIALOG_QUESTION, "reply_markup": download_dialog(service_name)}
