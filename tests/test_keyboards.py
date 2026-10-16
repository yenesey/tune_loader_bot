"""Тесты клавиатуры диалога и подписей (D10/D21)."""

import pytest

from tune_loader_bot.domain import services
from tune_loader_bot.telegram import captions, keyboards, texts

EXPECTED_LABELS = {
    "youtube": ["🎶Audio", "📺Video", "❌"],
    "rutube": ["🎶Audio", "📺Video", "❌"],
    "coub": ["🎶Audio", "📺Video", "❌"],
    "soundcloud": ["🎶Audio", "❌"],
    "yandex": ["🎶Audio", "❌"],
    "tiktok": ["📺Video", "❌"],
    "instagram": ["📺Video", "❌"],
}


def buttons(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


@pytest.mark.parametrize("name", sorted(EXPECTED_LABELS))
def test_dialog_buttons_per_service(name):
    markup = keyboards.download_dialog(name)

    assert [text for text, _ in buttons(markup)] == EXPECTED_LABELS[name]


@pytest.mark.parametrize("spec", services.SERVICE_SPECS, ids=lambda spec: spec.name)
def test_dialog_buttons_come_from_registry(spec):
    """Набор кнопок — ровно форматы сервиса из реестра плюс выход."""
    markup = keyboards.download_dialog(spec.name)

    data = [callback for _, callback in buttons(markup)]
    assert data == [*services.formats(spec.name), "exit"]


def test_dialog_message_uses_question_text():
    message = keyboards.download_dialog_message("youtube")

    assert message["text"] == texts.DIALOG_QUESTION
    assert message["reply_markup"] is not None


def test_unknown_service_raises_key_error():
    with pytest.raises(KeyError):
        keyboards.download_dialog("unknown-service")


def test_media_caption_contains_both_links():
    record = {
        "url": "https://youtu.be/x",
        "server_url": "https://example.test/media/2026/2026-10/T.mp3",
        "link_text": "Artist - Title",
    }

    caption = captions.media_caption(record)

    assert caption.startswith('<a href="https://youtu.be/x">#origin</a>')
    assert "#file" in caption
    assert record["server_url"] in caption


def test_link_message_uses_link_text():
    record = {
        "url": "https://youtu.be/x",
        "server_url": "https://example.test/media/2026/2026-10/T.mp3",
        "link_text": "Artist - Title",
    }

    text = captions.link_message(record)

    assert text.startswith('<a href="https://example.test/media/2026/2026-10/T.mp3">Artist - Title</a>')
    assert text.endswith('<a href="https://youtu.be/x">#origin</a>')
