"""Переходный доступ к настройкам в виде dict.

`bot.py` и `download.py` пока читают `SETTINGS["download-dir"]` и т.п. Этот
модуль отдаёт валидированные настройки (`config.Settings`) в старом формате.
Новый код должен использовать `config.get_settings()`; после фаз 3-6 модуль
удаляется (см. REFACTORING_PLAN.md, §2).
"""

from tune_loader_bot.config import get_settings

SETTINGS = get_settings().as_legacy_dict()
