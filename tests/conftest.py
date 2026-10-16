"""Общие фикстуры тестов.

Правила:
* тесты **не** читают рабочие `settings.json`/`downloads.db`/`cookies.txt` и не
  пишут в каталог загрузок: каждый тест живёт в своём `tmp_path`;
* `whisper` подменяется заглушкой, поэтому тесты не тянут torch и не грузят
  модель (см. REFACTORING_PLAN.md, D14);
* модули проекта импортируются заново в изолированном окружении, чтобы не
  «протекало» состояние между тестами (кэш `sys.modules`, `settings.SETTINGS`);
* живая `downloads.db` открывается **только** на чтение и только для того, чтобы
  снять с неё копию в `tmp_path`: миграции проверяются на копии.
"""

import importlib
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

# Модули проекта, которые зависят от CWD (settings.json) и должны
# переимпортироваться в каждом тесте.
PROJECT_MODULES = ("settings", "download", "database", "bot")

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_DB = REPO_ROOT / "downloads.db"

#: Схема живой базы **до** миграций: без `artist`/`channel`/`delivered`,
#: `user_id` допускает NULL, версия схемы 0 (см. D1/D2 в REFACTORING_PLAN.md).
LEGACY_SCHEMA = """
CREATE TABLE downloads (
    date DATETIME, user_id STRING, url STRING, video BOOLEAN,
    file_name STRING, file_size BIGINT, title STRING
);
CREATE UNIQUE INDEX idx_downloads ON downloads (url, video, user_id);
CREATE TABLE subscriptions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, user_id INTEGER, url STRING,
    keywords STRING, interval INTEGER, last_check DATETIME
);
"""


def _stat(path: Path):
    if not path.exists():
        return None
    info = path.stat()
    return (info.st_mtime_ns, info.st_size, info.st_ino)


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Каждый тест выполняется в своём CWD, а живая БД защищена от изменений.

    Это не формальность: `database.py` открывает базу относительно CWD, поэтому
    тест, забывший про изоляцию, пишет прямо в рабочую базу (инцидент
    16.10.2026, см. журнал в REFACTORING_PLAN.md). Guard сравнивает
    mtime/size/inode живой базы до и после теста.
    """
    before = _stat(LIVE_DB)
    monkeypatch.chdir(tmp_path)
    yield
    after = _stat(LIVE_DB)
    assert before == after, (
        f"тест изменил живую БД {LIVE_DB} (до: {before}, после: {after}); "
        "тест обязан работать в tmp_path"
    )


def _settings_for(tmp_path) -> dict:
    return {
        "telegram-api-token": "000000000:TEST-TOKEN",
        "po-token-gvs": "test-gvs",
        "po-token-web": "test-web",
        "download-dir": str(tmp_path / "media"),
        "server-root-url": "https://example.test/media",
        "users-list": [111, 222],
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Изолированное окружение: временный CWD с поддельным settings.json."""
    (tmp_path / "settings.json").write_text(
        json.dumps(_settings_for(tmp_path), ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def settings(project):
    """Настройки из временного `settings.json` (кэш `config` сброшен)."""
    config = importlib.import_module("tune_loader_bot.config")
    config.reset_settings_cache()
    return config.get_settings()


@pytest.fixture
def whisper_stub(monkeypatch):
    """Заглушка whisper: без torch и без загрузки модели (~1.4 ГБ)."""
    fake = types.ModuleType("whisper")
    fake.load_model = lambda *args, **kwargs: types.SimpleNamespace(
        transcribe=lambda *a, **k: {"text": ""}
    )
    monkeypatch.setitem(sys.modules, "whisper", fake)
    return fake


@pytest.fixture
def project_modules(project, whisper_stub):
    """Импортирует модули проекта так, будто они запущены из `project`."""
    # config остаётся в sys.modules (стабильная идентичность класса ConfigError),
    # но кэш настроек чистим: иначе settings.SETTINGS указывал бы на CWD
    # предыдущего теста.
    importlib.import_module("tune_loader_bot.config").reset_settings_cache()
    for name in PROJECT_MODULES:
        sys.modules.pop(name, None)
    yield {name: importlib.import_module(name) for name in PROJECT_MODULES}
    for name in PROJECT_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def bot_module(project_modules):
    return project_modules["bot"]


@pytest.fixture
async def storage(tmp_path):
    """Хранилище на временной базе (схема поднимается миграциями)."""
    storage_module = importlib.import_module("tune_loader_bot.storage.db")
    instance = await storage_module.Storage.open(tmp_path / "test.db")
    yield instance
    await instance.close()


@pytest.fixture
async def legacy_db(project):
    """Переходный dict-API `database.Database` (путь к базе берётся из настроек)."""
    sys.modules.pop("database", None)
    database = importlib.import_module("database")
    instance = await database.Database.create()
    yield instance
    await instance.close()
    sys.modules.pop("database", None)


@pytest.fixture
def legacy_db_path(tmp_path) -> Path:
    """Файл базы со старой (до миграций) схемой — как в живой `downloads.db`."""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def live_db_snapshot(tmp_path) -> Path:
    """Консистентная копия живой базы для проверки миграций.

    Снимается через backup API в режиме read-only, поэтому рабочая база не
    меняется; если файла нет (например, в CI) — тест пропускается.
    """
    if not LIVE_DB.exists():
        pytest.skip(f"нет живой базы {LIVE_DB}")
    target = tmp_path / "downloads.db"
    source = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def insert_legacy_row(path: Path, **fields) -> None:
    """Вставка строки в базу со старой схемой (для тестов миграций)."""
    row = {
        "date": "2026-10-16 12:00:00",
        "user_id": 111,
        "url": "https://example.com/track",
        "video": 0,
        "file_name": "track.mp3",
        "file_size": 1234,
        "title": "Track",
    }
    row.update(fields)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO downloads(date, user_id, url, video, file_name, file_size, title) "
            "VALUES(:date, :user_id, :url, :video, :file_name, :file_size, :title)",
            row,
        )
        connection.commit()
    finally:
        connection.close()


def read_rows(path: Path, sql: str = "SELECT * FROM downloads") -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()
