"""Тесты CLI миграций (`python -m tune_loader_bot.migrate`).

CLI — это то, чем применяется миграция к живой базе, поэтому проверяются все
режимы и коды возврата (на временных копиях, живая БД не участвует).
"""

import json
import sqlite3
from pathlib import Path

import pytest
from conftest import insert_legacy_row

from tune_loader_bot import migrate

FRESH_SETTINGS = {
    "telegram-api-token": "t",
    "download-dir": "/srv/media",
    "server-root-url": "https://example.test/media",
    "users-list": [111],
}


def user_version(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()


def test_report_mode_on_legacy_database(legacy_db_path, capsys):
    insert_legacy_row(legacy_db_path, user_id=None)
    insert_legacy_row(legacy_db_path, url="https://example.com/b", user_id="222")

    exit_code = migrate.main(["--report", "--db-path", str(legacy_db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "user_version: 0" in output
    assert "downloads: 2" in output
    assert "user_id_null: 1" in output
    assert user_version(legacy_db_path) == 0, "отчёт не должен менять базу"


def test_report_mode_is_json(legacy_db_path, capsys):
    insert_legacy_row(legacy_db_path, user_id=None)

    exit_code = migrate.main(["--report", "--json", "--db-path", str(legacy_db_path)])

    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["user_version"] == 0
    assert data["downloads"] == 1
    assert data["user_id_null"] == 1


def test_check_mode_shows_plan_without_changes(legacy_db_path, capsys):
    insert_legacy_row(legacy_db_path, user_id=None, url="https://example.com/a")

    exit_code = migrate.main(["--check", "--db-path", str(legacy_db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "не применялось" in output
    assert "user_id NULL -> 0: 1" in output
    assert user_version(legacy_db_path) == 0
    assert not list(legacy_db_path.parent.glob("*.bak"))


def test_apply_mode_migrates_with_backup(legacy_db_path, capsys):
    insert_legacy_row(legacy_db_path, url="https://example.com/a", user_id=None)
    insert_legacy_row(legacy_db_path, url="https://example.com/b", user_id=222)

    exit_code = migrate.main(["--apply", "--db-path", str(legacy_db_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "применено" in output
    assert "integrity_check: ok" in output
    assert "бэкап:" in output
    assert user_version(legacy_db_path) == 1
    assert len(list(legacy_db_path.parent.glob("*.bak"))) == 1

    values = sqlite3.connect(legacy_db_path).execute(
        "SELECT user_id FROM downloads ORDER BY url"
    ).fetchall()
    assert values == [(0,), (222,)]


def test_apply_mode_without_backup_flag(legacy_db_path, capsys):
    insert_legacy_row(legacy_db_path)

    exit_code = migrate.main(["--apply", "--no-backup", "--db-path", str(legacy_db_path)])

    assert exit_code == 0
    assert not list(legacy_db_path.parent.glob("*.bak"))


def test_apply_creates_database_from_scratch(tmp_path, capsys):
    target = tmp_path / "brand-new.db"

    exit_code = migrate.main(["--apply", "--db-path", str(target)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "будет создана" in output
    assert user_version(target) == 1


def test_missing_database_for_read_only_modes(tmp_path, capsys):
    missing = tmp_path / "absent.db"

    exit_code = migrate.main(["--report", "--db-path", str(missing)])

    assert exit_code == 1
    assert "база не найдена" in capsys.readouterr().err


def test_no_mode_prints_hint(tmp_path, capsys):
    exit_code = migrate.main(["--db-path", str(tmp_path / "x.db")])

    assert exit_code == 2
    assert "--report" in capsys.readouterr().err


def test_newer_schema_is_rejected(tmp_path, capsys):
    target = tmp_path / "future.db"
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE downloads (url TEXT)")
    connection.execute(f"PRAGMA user_version = {migrate.migrations.CURRENT_VERSION + 1}")
    connection.commit()
    connection.close()

    exit_code = migrate.main(["--apply", "--db-path", str(target)])

    assert exit_code == 1
    assert "новее" in capsys.readouterr().err


def test_db_path_defaults_to_settings(project, capsys):
    """Без `--db-path` путь берётся из настроек (ключ `db-path`)."""
    (project / "settings.json").write_text(json.dumps(FRESH_SETTINGS), encoding="utf-8")
    import importlib

    importlib.import_module("tune_loader_bot.config").reset_settings_cache()

    assert migrate.resolve_db_path(None) == Path("downloads.db")

    exit_code = migrate.main(["--apply"])

    assert exit_code == 0
    assert Path("downloads.db").exists()
    assert "brand-new" not in capsys.readouterr().out


def test_db_path_falls_back_when_settings_are_broken(project, monkeypatch):
    """Нет settings.json — CLI всё равно должен найти базу по умолчанию."""
    (project / "settings.json").unlink()
    import importlib

    importlib.import_module("tune_loader_bot.config").reset_settings_cache()

    assert migrate.resolve_db_path(None) == Path(migrate.DEFAULT_DB_PATH)


@pytest.mark.parametrize("flag", ["--report", "--check", "--apply"])
def test_modes_are_mutually_exclusive(flag, legacy_db_path):
    with pytest.raises(SystemExit):
        migrate.parse_args([flag, "--report" if flag != "--report" else "--check"])
