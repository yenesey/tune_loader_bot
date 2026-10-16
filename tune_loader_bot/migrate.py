"""CLI для миграций и отчётов по базе.

Примеры:

    python -m tune_loader_bot.migrate --report      # только чтение
    python -m tune_loader_bot.migrate --check       # что будет сделано
    python -m tune_loader_bot.migrate --apply       # применить (с копией базы)

`--report`/`--check` ничего не меняют: соединение не переводится в WAL и не
применяет миграции. `--apply` делает копию файла рядом (`*.bak`), применяет
миграции и печатает отчёт.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Sequence

import aiosqlite

from tune_loader_bot.config import ConfigError, get_settings
from tune_loader_bot.storage import migrations
from tune_loader_bot.storage.db import DEFAULT_DB_PATH, Storage


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tune_loader_bot.migrate",
        description="Миграции и отчёты по базе tune_loader_bot",
    )
    parser.add_argument("--db-path", default=None, help="путь к базе (по умолчанию из настроек)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="сводка по базе (только чтение)")
    mode.add_argument("--check", action="store_true", help="показать план миграции, не меняя базу")
    mode.add_argument("--apply", action="store_true", help="применить миграции (с копией базы)")
    parser.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    parser.add_argument("--no-backup", action="store_true", help="не делать копию базы")
    parser.add_argument("--log-level", default="INFO", help="уровень логирования (по умолчанию INFO)")
    return parser.parse_args(argv)


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    try:
        return Path(get_settings().db_path)
    except ConfigError:
        return Path(DEFAULT_DB_PATH)


async def run(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)

    if args.report or args.check:
        if not db_path.exists():
            print(f"база не найдена: {db_path}", file=sys.stderr)
            return 1
        connection = await aiosqlite.connect(db_path)
        connection.row_factory = aiosqlite.Row
        try:
            if args.report:
                data = await migrations.report(connection, db_path)
                if args.json:
                    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
                else:
                    for key, value in data.items():
                        print(f"{key}: {value}")
                return 0

            report = await migrations.migrate(connection, path=db_path, apply=False)
            if args.json:
                print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str))
            else:
                print(report.as_text())
            return 0
        finally:
            await connection.close()

    if not args.apply:
        print("укажите один из режимов: --report, --check или --apply", file=sys.stderr)
        return 2

    if not db_path.exists():
        print(f"база не найдена, будет создана: {db_path}")

    storage = await Storage.open(db_path, auto_migrate=False)
    try:
        report = await storage.migrate(backup=not args.no_backup)
        if args.json:
            print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str))
        else:
            print(report.as_text())
        return 0 if report.integrity in ("ok", "not checked") else 1
    finally:
        await storage.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        return asyncio.run(run(args))
    except (ConfigError, RuntimeError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
