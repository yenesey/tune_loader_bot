"""Общие фикстуры тестов.

Правила:
* тесты **не** читают рабочие `settings.json`/`downloads.db`/`cookies.txt` и не
  пишут в каталог загрузок: каждое тесто живёт в своём `tmp_path`;
* `whisper` подменяется заглушкой, поэтому тесты не тянут torch и не грузят
  модель (см. REFACTORING_PLAN.md, D14);
* модули проекта импортируются заново в изолированном окружении, чтобы не
  «протекало» состояние между тестами (singleton `Database`, кэш `sys.modules`).
"""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

# Модули проекта, которые зависят от CWD (settings.json) и должны
# переимпортироваться в каждом тесте.
PROJECT_MODULES = ("settings", "download", "database", "bot")

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_DB = REPO_ROOT / "downloads.db"


def _stat(path: Path):
    if not path.exists():
        return None
    info = path.stat()
    return (info.st_mtime_ns, info.st_size, info.st_ino)


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Каждый тест выполняется в своём CWD, а живая БД защищена от изменений.

    Это не формальность: `database.py` открывает `downloads.db` относительно
    CWD, поэтому тест, забывший про изоляцию, пишет прямо в рабочую базу
    (инцидент 16.10.2026, см. журнал в REFACTORING_PLAN.md). Guard сравнивает
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
    for name in PROJECT_MODULES:
        sys.modules.pop(name, None)
    yield {name: importlib.import_module(name) for name in PROJECT_MODULES}
    for name in PROJECT_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture
def bot_module(project_modules):
    return project_modules["bot"]


@pytest.fixture
async def db(tmp_path):
    """Свежее соединение с временной БД + сброс singleton'а Database."""
    database = importlib.import_module("database")
    database.Database._instance = None
    instance = await database.Database.create()
    opened = Path("downloads.db").resolve()
    assert opened.parent == tmp_path.resolve(), f"тест открыл не временную БД: {opened}"
    yield instance
    await instance._conn.close()
    database.Database._instance = None


async def add_live_columns(conn):
    """Дополняет свежую схему до состояния живой БД.

    Колонки `artist`, `channel`, `delivered` есть только в живой
    `downloads.db` (добавлены вручную) — см. D1 в REFACTORING_PLAN.md.
    """
    for column, column_type in (("artist", "TEXT"), ("channel", "TEXT"), ("delivered", "BOOLEAN")):
        await conn.execute(f"ALTER TABLE downloads ADD COLUMN {column} {column_type}")
    await conn.commit()


@pytest.fixture
async def db_live_schema(db):
    """Временная БД со схемой, как в живом `downloads.db` (до миграций фазы 2)."""
    await add_live_columns(db._conn)
    return db
