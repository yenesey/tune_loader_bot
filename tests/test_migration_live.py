"""Миграция на копии живой базы.

Сама `downloads.db` не открывается на запись: фикстура `live_db_snapshot` снимает
консистентную копию в `tmp_path` (backup API в режиме read-only), и все проверки
идут на ней. Если живой базы нет (CI) — тесты пропускаются.
"""

from pathlib import Path

from conftest import read_rows

from tune_loader_bot.storage import migrations
from tune_loader_bot.storage.db import Storage


def count(path: Path, sql: str) -> int:
    return int(read_rows(path, sql)[0][0])


async def test_migration_of_live_snapshot_keeps_data(live_db_snapshot):
    rows_before = count(live_db_snapshot, "SELECT COUNT(*) FROM downloads")
    subscriptions_before = count(live_db_snapshot, "SELECT COUNT(*) FROM subscriptions")
    delivered_before = count(live_db_snapshot, "SELECT COUNT(*) FROM downloads WHERE delivered = 1")
    assert rows_before > 0

    storage = await Storage.open(live_db_snapshot)
    try:
        connection = storage.connection
        assert await migrations.get_user_version(connection) == migrations.CURRENT_VERSION

        # ни одна запись не потеряна (кроме возможных дубликатов)
        assert await storage.count_downloads() <= rows_before
        assert count(live_db_snapshot, "SELECT COUNT(*) FROM downloads") == await storage.count_downloads()

        cursor = await connection.execute(
            "SELECT COUNT(*) FROM downloads WHERE user_id IS NULL OR typeof(user_id) != 'integer'"
        )
        assert (await cursor.fetchone())[0] == 0, "NULL/текстовые user_id должны быть нормализованы"

        cursor = await connection.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT 1 FROM downloads GROUP BY url, video, user_id HAVING COUNT(*) > 1)"
        )
        assert (await cursor.fetchone())[0] == 0

        assert count(live_db_snapshot, "SELECT COUNT(*) FROM downloads WHERE delivered = 1") == (
            delivered_before
        ), "флаг delivered не должен теряться"

        assert len(await storage.subscriptions()) == subscriptions_before

        cursor = await connection.execute("SELECT url, video FROM downloads LIMIT 1")
        row = await cursor.fetchone()
        found = await storage.find_download(row["url"], row["video"])
        assert found is not None
        assert found.url == row["url"]

        cursor = await connection.execute("PRAGMA integrity_check")
        assert (await cursor.fetchone())[0] == "ok"
    finally:
        await storage.close()


async def test_migration_of_live_snapshot_is_idempotent(live_db_snapshot):
    storage = await Storage.open(live_db_snapshot)
    await storage.close()
    rows_after_first = count(live_db_snapshot, "SELECT COUNT(*) FROM downloads")

    storage = await Storage.open(live_db_snapshot)
    try:
        report = await storage.migrate()
        assert report.applied is False
        assert report.from_version == migrations.CURRENT_VERSION
        assert await storage.count_downloads() == rows_after_first
    finally:
        await storage.close()


async def test_live_snapshot_migration_makes_backup(live_db_snapshot, tmp_path):
    storage = await Storage.open(live_db_snapshot)
    await storage.close()

    backups = sorted(tmp_path.glob("downloads.db.*.bak"))
    assert backups, "перед миграцией существующей базы должен создаваться бэкап"
