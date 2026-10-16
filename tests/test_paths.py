"""Характеризационные тесты раскладки файлов и публичных ссылок.

Поведение зафиксировано «как есть» на момент фазы 0: каталог ~93 ГБ раздаётся
веб-сервером, поэтому правила `sub_dir`/`enrich_info` менять нельзя без
миграции ссылок. Дефект D20 (мутация входа, склейка пути f-строкой) помечен
отдельным тестом.
"""

import os
from datetime import date, datetime

import pytest


@pytest.mark.parametrize(
    ("on_date", "expected"),
    [
        (datetime(2026, 10, 16, 12, 30), os.path.join("2026", "2026-10")),
        (datetime(2024, 1, 5), os.path.join("2024", "2024-01")),
        (datetime(2024, 12, 31, 23, 59), os.path.join("2024", "2024-12")),
    ],
)
def test_sub_dir(bot_module, on_date, expected):
    assert bot_module.sub_dir(on_date) == expected


def test_enrich_info_full_metadata(bot_module, project):
    info = {
        "artist": "Artist",
        "channel": "Channel",
        "title": "Title",
        "date": datetime(2026, 10, 16, 12, 0),
        "file_name": "Title.mp3",
    }

    result = bot_module.enrich_info(info)

    assert result["performer"] == "Artist"
    assert result["link_text"] == "Artist - Title"
    assert result["server_url"] == f"https://example.test/media/{os.path.join('2026', '2026-10')}/Title.mp3"
    assert result["file_path"] == str(project / "media" / "2026" / "2026-10" / "Title.mp3")


def test_enrich_info_falls_back_to_channel(bot_module):
    info = {"artist": None, "channel": "Channel", "title": "T", "date": datetime(2026, 1, 1), "file_name": "T.mp3"}

    result = bot_module.enrich_info(info)

    assert result["performer"] == "Channel"
    assert result["link_text"] == "Channel - T"


def test_enrich_info_without_performer_uses_title(bot_module):
    info = {"artist": None, "channel": None, "title": "T", "date": datetime(2026, 1, 1), "file_name": "T.mp3"}

    result = bot_module.enrich_info(info)

    assert result["performer"] is None
    assert result["link_text"] == "T"


def test_enrich_info_quotes_file_name_in_url(bot_module):
    info = {
        "artist": "A",
        "channel": None,
        "title": "T",
        "date": datetime(2026, 1, 1),
        "file_name": "Трек с пробелами & #1.mp3",
    }

    result = bot_module.enrich_info(info)

    assert " " not in result["server_url"]
    assert result["server_url"].endswith(".mp3")
    assert "#" not in result["server_url"]


@pytest.mark.parametrize("info", [None, {}, "not-a-dict", 42])
def test_enrich_info_returns_none_for_non_dict(bot_module, info):
    """Пустой dict тоже отдаёт None: проверка `if not info` срабатывает раньше
    обращения к ключам (в фазе 3 появится явная валидация схемы)."""
    assert bot_module.enrich_info(info) is None


def test_enrich_info_mutates_input_D20(bot_module):
    """D20: `enrich_info` дописывает поля прямо во входной словарь.

    Тест фиксирует текущее поведение; в фазе 3 функция перестанет мутировать
    вход (возврат нового объекта), тогда тест меняется на обратный.
    """
    info = {"artist": "A", "channel": None, "title": "T", "date": datetime(2026, 1, 1), "file_name": "T.mp3"}

    result = bot_module.enrich_info(info)

    assert result is info
    assert "server_url" in info
    assert "file_path" in info


def test_sub_dir_accepts_date_without_time(bot_module):
    """`date` из БД (не datetime) тоже работает — актуально для find_url."""
    assert bot_module.sub_dir(date(2026, 5, 4)) == os.path.join("2026", "2026-05")
