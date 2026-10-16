#!/usr/bin/python3
"""Совместимая точка входа для `python bot.py` (systemd-юнит).

Сборка приложения живёт в `tune_loader_bot.app`; `python -m tune_loader_bot`
делает ровно то же самое. Шим оставлен, чтобы уже установленный systemd-юнит
продолжал работать без изменений.
"""

from tune_loader_bot.app import main

if __name__ == "__main__":
    main()
