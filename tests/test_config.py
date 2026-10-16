"""Тесты конфигурации (фаза 1): валидация, значения по умолчанию, понятные ошибки."""

import importlib
import json

import pytest

from config import (
    DEFAULT_SETTINGS_FILE,
    SETTINGS_ENV_VAR,
    ConfigError,
    Settings,
    find_settings_file,
    get_settings,
    load_settings,
    reset_settings_cache,
)

REQUIRED = {
    "telegram-api-token": "123456:TEST",
    "download-dir": "/srv/media",
    "server-root-url": "https://example.test/media",
    "users-list": [111, 222],
}


@pytest.fixture(autouse=True)
def _clean_config_cache():
    """Кэш `get_settings()` — глобальный, между тестами его надо чистить."""
    reset_settings_cache()
    yield
    reset_settings_cache()


def write_settings(tmp_path, data, name="settings.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_minimal_settings_get_defaults(tmp_path):
    settings = load_settings(write_settings(tmp_path, REQUIRED))

    assert isinstance(settings, Settings)
    assert settings.telegram_api_token == "123456:TEST"
    assert settings.download_dir == "/srv/media"
    assert settings.users_list == [111, 222]
    # значения по умолчанию сохраняют текущее поведение кода
    assert settings.name_sep == "—"
    assert settings.db_path == "downloads.db"
    assert settings.cookies_file == "cookies.txt"
    assert settings.deno_path is None
    assert settings.max_concurrent_downloads == 2
    assert settings.tg_max_file_size == 50 * 1024 * 1024
    assert settings.watch_playlist_items == "1,2"
    assert settings.whisper_enabled is True
    assert settings.whisper_model == "turbo"
    assert settings.whisper_language == "ru"
    assert settings.log_level == "INFO"


def test_values_from_file_win_over_defaults(tmp_path):
    data = REQUIRED | {"db-path": "/tmp/other.db", "whisper-enabled": False, "log-level": "DEBUG"}

    settings = load_settings(write_settings(tmp_path, data))

    assert settings.db_path == "/tmp/other.db"
    assert settings.whisper_enabled is False
    assert settings.log_level == "DEBUG"


def test_as_legacy_dict_keeps_original_keys(tmp_path):
    settings = load_settings(write_settings(tmp_path, REQUIRED))

    legacy = settings.as_legacy_dict()

    assert legacy["download-dir"] == "/srv/media"
    assert legacy["telegram-api-token"] == "123456:TEST"
    assert legacy["name-sep"] == "—"
    assert legacy["db-path"] == "downloads.db"
    assert set(REQUIRED) <= set(legacy)


def test_find_settings_file_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv(SETTINGS_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert find_settings_file() == tmp_path / DEFAULT_SETTINGS_FILE


def test_env_var_selects_settings_file(tmp_path, monkeypatch):
    other = write_settings(tmp_path, REQUIRED | {"log-level": "ERROR"}, name="custom.json")
    monkeypatch.setenv(SETTINGS_ENV_VAR, str(other))

    assert find_settings_file() == other
    assert get_settings().log_level == "ERROR"


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_settings(tmp_path / "absent.json")

    message = str(exc.value)
    assert "absent.json" in message
    assert SETTINGS_ENV_VAR in message


def test_invalid_json_raises_config_error(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not a json", encoding="utf-8")

    with pytest.raises(ConfigError, match="некорректный JSON"):
        load_settings(path)


def test_json_array_is_rejected(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigError, match="JSON-объект"):
        load_settings(path)


def test_missing_required_key_is_reported(tmp_path):
    data = {key: value for key, value in REQUIRED.items() if key != "telegram-api-token"}

    with pytest.raises(ConfigError) as exc:
        load_settings(write_settings(tmp_path, data))

    assert "telegram-api-token" in str(exc.value)


def test_users_list_must_be_numbers(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_settings(write_settings(tmp_path, REQUIRED | {"users-list": ["not-a-number"]}))

    assert "users-list" in str(exc.value)


def test_numeric_strings_in_users_list_are_coerced(tmp_path):
    settings = load_settings(write_settings(tmp_path, REQUIRED | {"users-list": ["111"]}))

    assert settings.users_list == [111]


def test_unknown_keys_are_kept_with_warning(tmp_path, caplog):
    path = write_settings(tmp_path, REQUIRED | {"download_dir": "опечатка"})

    with caplog.at_level("WARNING"):
        settings = load_settings(path)

    assert "download_dir" in caplog.text
    assert settings.model_extra["download_dir"] == "опечатка"
    assert settings.as_legacy_dict()["download_dir"] == "опечатка"


def test_empty_users_list_warns(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        load_settings(write_settings(tmp_path, REQUIRED | {"users-list": []}))

    assert "users-list" in caplog.text


def test_get_settings_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv(SETTINGS_ENV_VAR, str(write_settings(tmp_path, REQUIRED)))

    first = get_settings()
    second = get_settings()
    assert first is second

    reset_settings_cache()
    third = get_settings()
    assert third is not first

    # кэш реально переиспользуется: правка файла без сброса не видна
    write_settings(tmp_path, REQUIRED | {"log-level": "DEBUG"})
    assert get_settings().log_level == "INFO"

    reset_settings_cache()
    assert get_settings().log_level == "DEBUG"


def test_legacy_settings_module_exposes_dict(project):
    """`settings.SETTINGS` остаётся dict со старыми ключами (переходный слой)."""
    importlib.import_module("config").reset_settings_cache()
    settings_module = importlib.import_module("settings")

    assert settings_module.SETTINGS["download-dir"] == str(project / "media")
    assert settings_module.SETTINGS["server-root-url"] == "https://example.test/media"
    assert settings_module.SETTINGS["users-list"] == [111, 222]
    assert settings_module.SETTINGS["name-sep"] == "—"
