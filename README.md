# tune_loader_bot

Telegram-бот: по ссылке скачивает аудио или видео (YouTube, SoundCloud, Яндекс.Музыка,
Rutube, Coub, TikTok, Instagram), складывает файлы в локальный веб-каталог, ведёт учёт
загрузок в SQLite, следит за YouTube-плейлистами и каналами по подпискам и умеет
расшифровывать голосовые сообщения (OpenAI Whisper).

## Возможности

- **Личные сообщения**: бот распознаёт ссылку и предлагает кнопки Audio/Video
  (набор зависит от сервиса из реестра).
- **Посты в канале**: ссылка скачивается сразу как аудио, без диалога.
- **Медиатека**: уже скачанное отдаётся из БД, файл при этом проверяется на диске.
- **Подписки**: watchdog раз в тик проверяет плейлисты/каналы, скачивает новое по
  фильтру ключевых слов и досылает недоставленное.
- **Голосовые**: расшифровка в текст (опционально, модель грузится при первом
  голосовом).
- **Файлы**: `{download-dir}/{YYYY}/{YYYY-MM}/{имя файла}`, публичная ссылка —
  `{server-root-url}/...` (каталог раздаётся веб-сервером).

## Требования

- Python 3.13, `ffmpeg`
- [Deno](https://deno.com/) — для расшифровки части форматов YouTube
  (путь задаётся настройкой `deno-path`, иначе ищется в `PATH`)
- `settings.json` в корне проекта (секреты, пути) — образец: `settings.example.json`
- `cookies.txt` для YouTube и Яндекс.Музыки (для Яндекса нужен аккаунт с Плюсом:
  без него API отдаёт только 30-секундное превью)
- Опционально: `openai-whisper` + `torch` (`requirements-whisper.txt`) — без них
  бот работает, распознавание голосовых выключается

## Установка и запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt            # рантайм
.venv/bin/pip install -r requirements-dev.txt         # тесты и линт
.venv/bin/pip install -r requirements-whisper.txt     # опционально: Whisper + torch
cp settings.example.json settings.json               # и заполнить своими значениями

.venv/bin/python -m tune_loader_bot                   # запуск
```

Установка systemd-юнита (нужен root, перезапускает сервис):

```bash
sudo ./install-service-systemd.sh
systemctl status tune-loader-bot
tail -f /var/log/tune-loader-bot.log
```

## База данных

SQLite (`downloads.db`, путь — настройка `db-path`), версия схемы в
`PRAGMA user_version`. Миграции идемпотентны и перед применением к существующей
базе делают копию `downloads.db.<timestamp>.bak`:

```bash
.venv/bin/python -m tune_loader_bot.migrate --report   # сводка (только чтение)
.venv/bin/python -m tune_loader_bot.migrate --check    # что будет сделано
.venv/bin/python -m tune_loader_bot.migrate --apply    # применить
```

## Разработка

```bash
.venv/bin/python -m pytest                            # тесты
.venv/bin/python -m pytest --cov --cov-report=term    # с покрытием
.venv/bin/ruff check .                                # линт
```

Тесты герметичны: работают во временном каталоге, не требуют сети и Telegram,
подменяют `whisper`, `yt-dlp` и сессию aiogram, а живая `downloads.db` защищена
guard'ом в `tests/conftest.py` (тест, изменивший рабочую базу, падает).

## Структура

```
tune_loader_bot/
├── __main__.py            # python -m tune_loader_bot
├── app.py                 # композиция: сборка, запуск, остановка
├── config.py              # settings.json → типизированный Settings
├── logging_setup.py       # логи (уровень из настроек)
├── library.py             # медиатека: найти готовое или скачать
├── migrate.py             # CLI миграций
├── domain/                # модели и реестр сервисов (без I/O)
├── media/                 # раскладка файлов, yt-dlp и загрузка Яндекс.Музыки
├── storage/               # SQLite: соединение и миграции
├── telegram/              # тексты, клавиатура, доставка, обработчики, голосовые
├── jobs/                  # watchdog подписок
└── transcription/         # Whisper (ленивая загрузка модели)
```

## Настройки

`settings.json` (полный список с комментариями — в `settings.example.json`):
`telegram-api-token`, `users-list`, `download-dir`, `server-root-url`,
`po-token-gvs`, `po-token-web`, `cookies-file`, `deno-path`, `db-path`,
`filename-template`, `max-concurrent-downloads`, `tg-max-file-size`,
`watch-playlist-items`, `watch-tick-seconds`, `whisper-enabled`,
`whisper-model`, `whisper-language`, `log-level`.

Путь к файлу настроек можно переопределить переменной `TUNE_LOADER_SETTINGS`.

## План рефакторинга

История поэтапного рефакторинга, карта дефектов (D1-D25) и журнал прогресса —
в [REFACTORING_PLAN.md](REFACTORING_PLAN.md).
