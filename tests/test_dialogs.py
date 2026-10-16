"""Характеризационные тесты диалога выбора формата (D10).

Внешнее поведение (текст кнопок и callback_data) фиксируется как есть. Внутри
`create_download_dialog` значения для soundcloud/yandex/tiktok/instagram — это
строки `("audio")` вместо кортежей, поэтому фильтр `btn in options[key]` сейчас
работает как поиск подстроки. В фазе 4 это заменяется реестром сервисов; тогда
тесты дополняются проверками самого реестра.
"""

import pytest

DIALOG_TEXT = "Может качнем эту ☝ ссылку?"


def _buttons(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


@pytest.mark.parametrize(
    ("service", "expected"),
    [
        ("youtube", [("🎶Audio", "audio"), ("📺Video", "video"), ("❌", "exit")]),
        ("rutube", [("🎶Audio", "audio"), ("📺Video", "video"), ("❌", "exit")]),
        ("coub", [("🎶Audio", "audio"), ("📺Video", "video"), ("❌", "exit")]),
        ("soundcloud", [("🎶Audio", "audio"), ("❌", "exit")]),
        ("yandex", [("🎶Audio", "audio"), ("❌", "exit")]),
        ("tiktok", [("📺Video", "video"), ("❌", "exit")]),
        ("instagram", [("📺Video", "video"), ("❌", "exit")]),
    ],
)
def test_dialog_buttons_per_service(bot_module, service, expected):
    dialog = bot_module.create_download_dialog(service)

    assert dialog["text"] == DIALOG_TEXT
    assert _buttons(dialog["reply_markup"]) == expected


def test_dialog_unknown_service_raises_key_error(bot_module):
    """Неизвестный сервис → KeyError: вызывающий обязан проверить `check_url`."""
    with pytest.raises(KeyError):
        bot_module.create_download_dialog("unknown-service")
