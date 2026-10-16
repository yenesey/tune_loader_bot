"""Единая настройка логирования.

D25: раньше `logging.basicConfig` вызывался только под `if __name__ == "__main__"`
в `bot.py`, уровень был захардкожен (`INFO`), а ошибки логировались через
`logging.error(traceback.format_exc())`. Теперь формат и уровень задаются в одном
месте, уровень берётся из настроек (`log-level`), а для исключений используется
`LOGGER.exception` (со стеком и без ручной сборки строки).
"""

from __future__ import annotations

import logging

from tune_loader_bot.config import Settings

DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"
FALLBACK_LEVEL = logging.INFO


def resolve_level(value: str | int | None) -> int:
    """Уровень логирования из настроек: `"DEBUG"`, `"info"`, `20` или `None`."""
    if value is None or value == "":
        return FALLBACK_LEVEL
    if isinstance(value, int):
        return value
    level = logging.getLevelNamesMapping().get(str(value).strip().upper())
    if level is None:
        logging.getLogger(__name__).warning(
            "неизвестный уровень логирования %r — используется %s",
            value,
            logging.getLevelName(FALLBACK_LEVEL),
        )
        return FALLBACK_LEVEL
    return level


def setup_logging(settings: Settings | None = None, *, force: bool = False) -> None:
    """Настраивает корневой логгер (идемпотентно, если `force=False`)."""
    level = resolve_level(getattr(settings, "log_level", None))
    root = logging.getLogger()
    if root.handlers and not force:
        root.setLevel(level)
        return
    logging.basicConfig(
        format=DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT, level=level, force=force
    )
    # yt-dlp пишет прогресс через `logger.info`, а мы мапим его в DEBUG
    # (`media.ytdlp.YdlLogAdapter`), поэтому отдельный уровень ему не нужен.
    logging.getLogger("yt-dlp").setLevel(min(level, logging.INFO))
