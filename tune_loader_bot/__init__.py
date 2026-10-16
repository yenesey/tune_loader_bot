"""tune_loader_bot — пакет приложения.

Появляется по фазам (см. REFACTORING_PLAN.md): в фазе 2 сюда переехали `config`
и новый слой `storage`/`domain`; легаси-модули (`bot.py`, `download.py`,
`database.py`, `settings.py`) пока остаются плоскими и постепенно заменяются.
"""
