"""Тесты миграций схемы: чистая база, легаси-база, идемпотентность, бэкапы."""

import sqlite3
from pathlib import Path

import aiosqlite
import pytest
from conftest import insert_legacy_row, read_rows

from tune_loader_bot.storage import migrations
from tune_loader_bot.storage.db import Storage


def add_column(path: Path, ddl: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"ALTER TABLE downloads ADD COLUMN {ddl}")
        connection.commit()
    finally:
        connection.close()


def execute(path: Path, sql: str, params: tuple = ()) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


async def open_plain(path: Path) -> aiosqlite.Connection:
    connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    return connection


def backups(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob("*.bak"))


async def test_fresh_file_gets_current_schema(tmp_path):
    """D1: новая база поднимается с полной схемой и без бэкапа."""
    path = tmp_path / "fresh.db"

    storage = await Storage.open(path)
    try:
        columns = await migrations.table_columns(storage.connection, "downloads")
        assert {"artist", "channel", "delivered"} <= set(columns)
        assert await migrations.get_user_version(storage.connection) == migrations.CURRENT_VERSION
        assert await storage.count_downloads() == 0
    finally:
        await storage.close()

    assert backups(tmp_path) == []


async def test_empty_file_is_migrated(tmp_path):
    """Пустой (0 байт) файл — тоже «с нуля», копировать нечего."""
    path = tmp_path / "empty.db"
    path.touch()

    storage = await Storage.open(path)
    try:
        assert await migrations.get_user_version(storage.connection) == migrations.CURRENT_VERSION
    finally:
        await storage.close()

    assert backups(tmp_path) == []


async def test_nulls_are_normalized_and_columns_added(tmp_path, legacy_db_path):
    """D1/D2: NULL user_id → 0, добавлены колонки, перед миграцией сделан бэкап.

    Колонка объявлена как `STRING`, а это NUMERIC-affinity, поэтому SQLite сам
    хранит «222» как 222 (то же самое видно в живой базе). Схема с TEXT-affinity
    проверяется в `test_non_integer_user_ids_are_reported`.
    """
    insert_legacy_row(legacy_db_path, url="https://example.com/a", user_id=None)
    insert_legacy_row(legacy_db_path, url="https://example.com/b", user_id="222")

    connection = await open_plain(legacy_db_path)
    try:
        report = await migrations.migrate(connection, path=legacy_db_path)
    finally:
        await connection.close()

    assert report.applied is True
    assert report.null_user_ids_fixed == 1
    assert report.non_numeric_user_ids == 0
    assert report.rows_before == report.rows_after == 2
    assert report.integrity == "ok"
    assert report.backup_path is not None and report.backup_path.exists()
    assert read_rows(legacy_db_path, "SELECT user_id, typeof(user_id) FROM downloads ORDER BY url") == [
        (0, "integer"),
        (222, "integer"),
    ]
    columns = {row[1] for row in read_rows(legacy_db_path, "PRAGMA table_info(downloads)")}
    assert {"artist", "channel", "delivered"} <= columns


async def test_non_integer_user_ids_are_reported(tmp_path):
    """TEXT-affinity не даёт хранить число, поэтому такие строки не «чинятся»,
    а попадают в отчёт миграции (данные важнее косметики)."""
    path = tmp_path / "text_ids.db"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE downloads (
                date DATETIME, user_id TEXT, url TEXT, video BOOLEAN,
                file_name TEXT, file_size BIGINT, title TEXT
            );
            CREATE TABLE subscriptions (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, user_id INTEGER, url TEXT,
                keywords TEXT, interval INTEGER, last_check DATETIME
            );
            INSERT INTO downloads(url, user_id, video) VALUES('https://example.com/t', '222', 0);
            """
        )
        connection.commit()
    finally:
        connection.close()

    connection = await open_plain(path)
    try:
        report = await migrations.migrate(connection, path=path)
    finally:
        await connection.close()

    assert report.non_numeric_user_ids == 1
    assert any("нечислов" in note for note in report.notes)
    assert read_rows(path, "SELECT user_id, typeof(user_id) FROM downloads") == [("222", "text")]


async def test_duplicates_removed_and_delivered_propagated(tmp_path, legacy_db_path):
    """Дубликаты схлопываются в самую свежую запись, `delivered` не теряется.

    Дубликаты возможны именно у строк с `user_id IS NULL` (посты канала): NULL в
    SQLite не участвует в UNIQUE-индексе, поэтому три одинаковые ссылки лежат
    рядом. После миграции остаётся одна свежая запись с `user_id = 0`.
    """
    add_column(legacy_db_path, "delivered BOOLEAN")
    insert_legacy_row(legacy_db_path, url="dup", user_id=None, date="2026-01-01 00:00:00", title="old")
    insert_legacy_row(legacy_db_path, url="dup", user_id=None, date="2026-02-01 00:00:00", title="middle")
    insert_legacy_row(legacy_db_path, url="dup", user_id=None, date="2026-03-01 00:00:00", title="new")
    execute(legacy_db_path, "UPDATE downloads SET delivered = 1 WHERE title = 'old'")

    connection = await open_plain(legacy_db_path)
    try:
        report = await migrations.migrate(connection, path=legacy_db_path)
    finally:
        await connection.close()

    assert report.duplicates_removed == 2
    assert report.rows_before == 3 and report.rows_after == 1
    assert report.null_user_ids_fixed == 3
    assert read_rows(legacy_db_path, "SELECT user_id, title, delivered FROM downloads") == [
        (0, "new", 1)
    ]


async def test_migration_is_idempotent(tmp_path, legacy_db_path):
    insert_legacy_row(legacy_db_path, user_id=None)

    connection = await open_plain(legacy_db_path)
    try:
        first = await migrations.migrate(connection, path=legacy_db_path)
        second = await migrations.migrate(connection, path=legacy_db_path)
    finally:
        await connection.close()

    assert first.applied is True
    assert second.applied is False
    assert second.from_version == migrations.CURRENT_VERSION
    assert second.rows_after == 1
    assert len(backups(tmp_path)) == 1, "повторная миграция не должна плодить бэкапы"


async def test_check_mode_does_not_touch_database(tmp_path, legacy_db_path):
    """`--check` только считает: версия, данные и режим журнала не меняются."""
    insert_legacy_row(legacy_db_path, user_id=None, url="https://example.com/a")
    insert_legacy_row(legacy_db_path, user_id=None, url="https://example.com/a", title="second")
    insert_legacy_row(legacy_db_path, user_id=111, url="https://example.com/b")

    connection = await open_plain(legacy_db_path)
    try:
        report = await migrations.migrate(connection, path=legacy_db_path, apply=False)
        version_cursor = await connection.execute("PRAGMA user_version")
        version = (await version_cursor.fetchone())[0]
        mode_cursor = await connection.execute("PRAGMA journal_mode")
        journal_mode = (await mode_cursor.fetchone())[0]
    finally:
        await connection.close()

    assert report.applied is False
    assert report.null_user_ids_fixed == 2
    assert report.duplicates_removed == 1
    assert version == 0
    assert journal_mode.lower() != "wal"
    assert backups(tmp_path) == []
    # данные остались нетронутыми: NULL-ы и 111 на месте
    assert read_rows(legacy_db_path, "SELECT user_id FROM downloads ORDER BY rowid") == [
        (None,),
        (None,),
        (111,),
    ]


async def test_check_mode_on_missing_tables(tmp_path):
    path = tmp_path / "blank.db"
    path.touch()

    connection = await open_plain(path)
    try:
        report = await migrations.migrate(connection, path=path, apply=False)
    finally:
        await connection.close()

    assert report.applied is False
    assert any("пустая" in note for note in report.notes)


async def test_newer_schema_version_is_rejected(tmp_path, legacy_db_path):
    execute(legacy_db_path, f"PRAGMA user_version = {migrations.CURRENT_VERSION + 5}")

    connection = await open_plain(legacy_db_path)
    try:
        with pytest.raises(RuntimeError, match="новее"):
            await migrations.migrate(connection, path=legacy_db_path)
    finally:
        await connection.close()


async def test_subscriptions_survive_migration(tmp_path, legacy_db_path):
    execute(
        legacy_db_path,
        "INSERT INTO subscriptions(user_id, url, keywords, interval, last_check) VALUES(?, ?, ?, ?, ?)",
        (114213445, "https://www.youtube.com/@channel/videos", "mix", 3600, "2026-10-16 17:42:50.943199"),
    )

    connection = await open_plain(legacy_db_path)
    try:
        await migrations.migrate(connection, path=legacy_db_path)
    finally:
        await connection.close()

    storage = await Storage.open(legacy_db_path, auto_migrate=False)
    try:
        subscriptions = await storage.subscriptions()
    finally:
        await storage.close()

    assert len(subscriptions) == 1
    assert subscriptions[0].url == "https://www.youtube.com/@channel/videos"
    assert subscriptions[0].keywords == "mix"


async def test_report_summarizes_legacy_database(legacy_db_path):
    insert_legacy_row(legacy_db_path, user_id=None, url="https://example.com/a")
    insert_legacy_row(legacy_db_path, user_id=None, url="https://example.com/a", title="second")
    insert_legacy_row(legacy_db_path, user_id=111, url="https://example.com/b")

    connection = await open_plain(legacy_db_path)
    try:
        data = await migrations.report(connection, legacy_db_path)
    finally:
        await connection.close()

    assert data["user_version"] == 0
    assert data["downloads"] == 3
    assert data["user_id_null"] == 2
    assert data["duplicate_groups"] == 1
    assert "delivered" not in data, "колонки delivered в старой схеме ещё нет"
