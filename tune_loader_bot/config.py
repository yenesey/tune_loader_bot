"""Конфигурация приложения: чтение и валидация `settings.json`.

Раньше настройки читались как `json.load(open('settings.json'))` прямо на
импорте, без проверок (D17): опечатка или отсутствующий ключ приводили к
`KeyError` где-то в глубине обработчика, а не к внятной ошибке на старте.

Здесь:
* `Settings` — типизированная модель (pydantic v2, уже есть транзитивно через
  aiogram), ключи JSON сохраняются через alias'ы (`download-dir` → `download_dir`);
* `load_settings()` — чистая функция чтения конкретного файла;
* `get_settings()` — кэшированный синглтон для рантайма;
* `Settings.as_legacy_dict()` — переходный dict со старыми ключами, чтобы
  `bot.py`/`download.py` продолжали работать через `settings.SETTINGS`
  до фаз 3-6 (см. REFACTORING_PLAN.md).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_SETTINGS_FILE = "settings.json"
SETTINGS_ENV_VAR = "TUNE_LOADER_SETTINGS"
DEFAULT_TG_MAX_FILE_SIZE = 50 * 1024 * 1024


class ConfigError(RuntimeError):
    """Настройки отсутствуют или невалидны. Текст пригоден для лога и человеку."""


class Settings(BaseModel):
    """Настройки бота.

    Обязательны только те поля, без которых приложение не работает; остальные
    имеют значения по умолчанию, сохраняющие текущее поведение.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, str_strip_whitespace=True)

    # --- обязательные ---
    telegram_api_token: str = Field(alias="telegram-api-token")
    download_dir: str = Field(alias="download-dir")
    server_root_url: str = Field(alias="server-root-url")
    users_list: list[int] = Field(alias="users-list")

    # --- были в настройках, но необязательные ---
    po_token_gvs: str = Field(default="", alias="po-token-gvs")
    po_token_web: str = Field(default="", alias="po-token-web")
    name_sep: str = Field(default="—", alias="name-sep")

    # --- новые ключи (значения по умолчанию = текущее поведение кода) ---
    db_path: str = Field(default="downloads.db", alias="db-path")
    cookies_file: str = Field(default="cookies.txt", alias="cookies-file")
    deno_path: str | None = Field(default=None, alias="deno-path")
    max_concurrent_downloads: int = Field(default=2, ge=1, alias="max-concurrent-downloads")
    tg_max_file_size: int = Field(default=DEFAULT_TG_MAX_FILE_SIZE, ge=1, alias="tg-max-file-size")
    watch_playlist_items: str = Field(default="1,2", alias="watch-playlist-items")
    whisper_enabled: bool = Field(default=True, alias="whisper-enabled")
    whisper_model: str = Field(default="turbo", alias="whisper-model")
    whisper_language: str = Field(default="ru", alias="whisper-language")
    log_level: str = Field(default="INFO", alias="log-level")

    def as_legacy_dict(self) -> dict[str, Any]:
        """Плоский dict с исходными (дефисными) ключами.

        Только для совместимости со старым кодом; новый код должен читать поля
        модели напрямую.
        """
        data: dict[str, Any] = {
            field.alias: getattr(self, name) for name, field in type(self).model_fields.items()
        }
        data.update(self.model_extra or {})
        return data


def find_settings_file(path: str | os.PathLike[str] | None = None) -> Path:
    """Путь к файлу настроек: аргумент → $TUNE_LOADER_SETTINGS → `settings.json`.

    Возвращается абсолютный путь: сообщения об ошибках не должны зависеть от
    того, откуда был запущен процесс.
    """
    if path is not None:
        return Path(path).expanduser().resolve()
    from_env = os.environ.get(SETTINGS_ENV_VAR)
    if from_env:
        return Path(from_env).expanduser().resolve()
    return Path(DEFAULT_SETTINGS_FILE).resolve()


def _warn_about_suspicious(settings: Settings, settings_file: Path) -> None:
    unknown = sorted(settings.model_extra or {})
    if unknown:
        logging.warning(
            "%s: неизвестные ключи настроек (оставлены как есть): %s",
            settings_file,
            ", ".join(unknown),
        )
    if not settings.users_list:
        logging.warning(
            "%s: 'users-list' пуст — бот будет игнорировать все апдейты", settings_file
        )
    if not settings.po_token_gvs or not settings.po_token_web:
        logging.warning(
            "%s: 'po-token-gvs'/'po-token-web' не заданы — часть видео YouTube "
            "может не скачиваться",
            settings_file,
        )


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Читает и валидирует файл настроек. Бросает `ConfigError` с понятным текстом."""
    settings_file = find_settings_file(path)
    if not settings_file.is_file():
        raise ConfigError(
            f"не найден файл настроек '{settings_file}' (текущий каталог: {Path.cwd()}); "
            f"создайте его по образцу settings.example.json или укажите путь в ${SETTINGS_ENV_VAR}"
        )

    try:
        raw = json.loads(settings_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"'{settings_file}': некорректный JSON — {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"'{settings_file}': ожидался JSON-объект с настройками")

    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"'{settings_file}': настройки не прошли валидацию:\n{exc}") from exc

    _warn_about_suspicious(settings, settings_file)
    return settings


_settings: Settings | None = None


def get_settings(*, reload: bool = False) -> Settings:
    """Кэшированный доступ к настройкам (путь берётся из окружения/CWD)."""
    global _settings
    if _settings is None or reload:
        _settings = load_settings()
    return _settings


def reset_settings_cache() -> None:
    """Сброс кэша (нужен тестам и смене CWD)."""
    global _settings
    _settings = None
