# tune_loader_bot — краткое описание проекта

Telegram-бот, который по ссылке скачивает аудио или видео с популярных ресурсов,
хранит файлы в локальном веб-каталоге, ведёт учёт загрузок в SQLite и умеет
автоматически следить за YouTube-плейлистами/каналами по подпискам. Дополнительно
расшифровывает голосовые сообщения в текст (OpenAI Whisper).

Весь код — в пакете `tune_loader_bot/`; в корне только настройки, скрипты и
документация. Запуск — `python -m tune_loader_bot` (systemd-юнит использует именно
его). Есть тесты (pytest) и линт (ruff), CI на GitHub Actions. Поэтапный
рефакторинг завершён (фазы 0-8): план, карта дефектов и журнал прогресса — в
`REFACTORING_PLAN.md`.

## Стек

- Python 3.13 (`.venv/` в корне)
- Зависимости разделены: `requirements.txt` (рантайм), `requirements-dev.txt`
  (pytest, pytest-asyncio, pytest-cov, ruff), `requirements-whisper.txt`
  (openai-whisper + torch — тяжёлые, ставятся отдельно)
- aiogram 3.x (long polling), aiosqlite, yt-dlp (+ yt-dlp-ejs, требует Deno)
- openai-whisper + torch — **опционально** (`requirements-whisper.txt`): без них
  бот работает, а распознавание голосовых выключается
- ffmpeg (используется постпроцессорами yt-dlp для конвертации в mp3/mp4)

## Файлы

| Файл | Роль |
|---|---|
| `tune_loader_bot/media/` | `paths.py` (раскладка файлов, публичные ссылки), `ytdlp.py` (опции, загрузка, разбор плейлиста) |
| `tune_loader_bot/telegram/` | Тексты, клавиатура, подписи, доставка, обработчики, голосовые, middleware, `create_dispatcher` |
| `tune_loader_bot/library.py` | Медиатека: найти готовое или скачать и сохранить (прикладной слой) |
| `tune_loader_bot/jobs/` | `watchdog.py` — фоновый обход подписок (`SubscriptionWatcher`) |
| `tune_loader_bot/transcription/` | `whisper_service.py` — распознавание речи (ленивая загрузка модели) |
| `tune_loader_bot/` | Пакет: `app.py` (композиция), `__main__.py`, `config.py`, `logging_setup.py`, `library.py`, `migrate.py` и подпакеты `domain/`, `media/`, `storage/`, `telegram/`, `jobs/`, `transcription/` |
| `pyproject.toml` | Конфиг ruff, pytest и coverage (проект запускается из корня, без установки пакета) |
| `.github/workflows/ci.yml` | CI: `ruff check` + `pytest` с порогом покрытия 85% |
| `README.md` | Описание проекта, запуск, миграции, структура |
| `tests/` | pytest-тесты: герметичные (временный CWD), живая БД защищена guard'ом |
| `REFACTORING_PLAN.md` | План рефакторинга, карта дефектов (D1-D25) и журнал прогресса |
| `settings.example.json` | Образец настроек без секретов (в git) |
| `settings.json` | **Секреты и пути** (в `.gitignore`): токен бота, po-token'ы YouTube, `download-dir`, `server-root-url`, `users-list` |
| `cookies.txt` | Cookies для yt-dlp (в `.gitignore`) |
| `downloads.db` | Живая база: 2065 записей о загрузках (в `.gitignore`) |
| `install-service-systemd.sh` | Установка/переустановка systemd-юнита `tune-loader-bot.service` |
| `tune-loader-bot.log` | Симлинк на `/var/log/tune-loader-bot.log` |

## Как работает

1. **Доступ.** `SecurityMiddleware` (outer middleware на `dp.update`) молча отбрасывает
   апдейты от user_id, которых нет в `settings.users_list` (посты канала проходят:
   у них нет `event_from_user`).
2. **Личные сообщения**: `services.match()` определяет сервис, бот отвечает
   «Может качнем эту ☝ ссылку?» с кнопками Audio/Video (набор берётся из реестра).
   Ссылка и адрес сообщения сохраняются в FSM-данных, состояние — `DownloadDialog.select_action`.
3. **Callback** кнопки: удаляет вопрос, берёт ссылку из FSM-данных, скачивает и правит
   сообщение «Processing…»; если состояние потеряно (рестарт) — отвечает «ссылка устарела».
4. **Посты в канале** (`channel_post`): ссылка сразу скачивается как аудио, без диалога
   (`user_id = 0` в БД). `delivered=0` остаётся — такие файлы досылает watchdog.
5. **`MediaLibrary`** (`library.py`): сначала ищет запись в БД и проверяет, что файл есть
   на диске, иначе качает (`media/ytdlp.py`) и сохраняет. **`Delivery`** решает, отправить
   файл (≤ `tg-max-file-size`) или ссылку на `server-root-url`, и помечает `delivered`.
   Исходное сообщение удаляется best-effort.
6. **Файлы на диске**: `{download-dir}/{YYYY}/{YYYY-MM}/{title}` — `sub_dir()` + `enrich_info()`
   формируют `file_path` и публичный `server_url` (каталог раздаётся веб-сервером,
   сейчас ~93 ГБ).
7. **`SubscriptionWatcher`** (`jobs/watchdog.py`, тик `watch-tick-seconds`, по умолчанию
   60 с): для каждой подписки раз в `interval` секунд разбирает плейлист
   (`watch-playlist-items`, по умолчанию `"1,2"`), фильтрует записи по `keywords`
   (без учёта регистра, разделитель `,`, префикс `-` — исключение), скачивает новое как
   аудио и досылает недоставленное (`delivered=0`). `last_check` двигается **после**
   успешной проверки, ошибка одной подписки не мешает остальным.
8. **Голосовые**: `TelegramVoiceTranscriber` скачивает файл во временный каталог и
   отдаёт `WhisperTranscriber` — модель грузится **при первом голосовом** (в потоке,
   с `Semaphore(1)`), язык/модель из настроек, `whisper-enabled` выключает фичу,
   а отсутствие пакета не роняет бота. Ответ — `message.reply`.
9. **Запуск**: `.venv/bin/python -m tune_loader_bot` из корня проекта; настройки и
   БД ищутся относительно CWD, путь к настройкам —
   `$TUNE_LOADER_SETTINGS`. Композиция и остановка — в `tune_loader_bot/app.py`:
   по SIGTERM aiogram завершает polling, приложение закрывает БД и отменяет watchdog.
   В бою — `tune-loader-bot.service` от пользователя `denis`, лог
   `/var/log/tune-loader-bot.log` (`StandardOutput=append:`).

## Схема БД (`downloads.db`)

- Версия схемы — `PRAGMA user_version` (сейчас **1**); миграции живут в
  `tune_loader_bot/storage/migrations.py`, идемпотентны и делают копию базы.
- `downloads(date, user_id, url, video, file_name, file_size, title, artist, channel, delivered)`
  + `UNIQUE INDEX idx_downloads(url, video, user_id)`.
- `user_id` — всегда число; `0` означает «служебного» пользователя для постов
  канала (раньше был NULL, а NULL в SQLite не участвует в UNIQUE-индексе, из-за
  чего копились дубликаты). `video`/`delivered` — 0/1.
- `subscriptions(id, user_id, url, keywords, interval, last_check)`.
- Соединение открывается с `journal_mode=WAL`, `busy_timeout=5000`,
  `foreign_keys=ON`, `row_factory=aiosqlite.Row`.
- Представление `v_users` (в коде не используется) — маппинг user_id на имена.

Проверка и миграция:

```bash
.venv/bin/python -m tune_loader_bot.migrate --report   # сводка (только чтение)
.venv/bin/python -m tune_loader_bot.migrate --check    # что будет сделано
.venv/bin/python -m tune_loader_bot.migrate --apply    # применить (копия *.bak)
```

## Запуск и деплой

```bash
source .venv/bin/activate
python -m tune_loader_bot                       # локальный запуск
sudo ./install-service-systemd.sh               # установка systemd-юнита
systemctl status|restart tune-loader-bot
journalctl/tail -f tune-loader-bot.log
```

Требуется `settings.json` в CWD, `cookies.txt` для YouTube и Deno
(`deno-path` → `PATH` → `~/.deno/bin/deno`).
Миграции базы — `.venv/bin/python -m tune_loader_bot.migrate --report|--check|--apply`.

## Известные особенности и подводные камни

Актуальный список дефектов (D1-D25) и статус закрытия — в `REFACTORING_PLAN.md`
(§1 и §8, там же колонка «Закрыт»). Фазы 0-8 закрыты: из D1-D25 открытым остаётся
только D5 (имена файлов, см. ниже).

Знание о сервисах — только в `tune_loader_bot/domain/services.py` (домены,
форматы, селекторы yt-dlp). Не дублировать регулярки и наборы форматов в
обработчиках.

Важно про yt-dlp: значения в `extractor_args` передаются **только списками**
(`{"youtube": {"player_js_version": ["actual"]}}`) — строку yt-dlp разбирает по
символам и пишет в лог `Invalid player JS version "a"`.

Deno ищется в порядке `deno-path` → `PATH` → `~/.deno/bin/deno`. Под systemd
`PATH` минимальный (`~/.deno/bin` в него не входит), поэтому без настройки
`deno-path` работает только третий шаг — на нём YouTube и держится (регрессия
16.10.2026: без JS-рантайма yt-dlp не решает n-challenge и пишет
`Requested format is not available`).

Осталось решить владельцу:

- Имя файла задаётся настройкой `filename-template` (по умолчанию
  `%(title).100s`): одноимённые видео затирают друг друга, пока владелец не
  включит вариант с `%(id)s` (D5).
- Секреты (`settings.json`, `cookies.txt`, `downloads.db`, лог) в `.gitignore` —
  **не коммитить и не печатать их содержимое**. Копии базы `downloads.db.*.bak`
  тоже в `.gitignore`, но содержат реальные данные — не публиковать.

## Соглашения для агентов

- Зависимости передаются явно (`HandlerContext`, `MediaLibrary`, `Storage`):
  глобальный `SETTINGS` в новом коде не используется. Без новых зависимостей без
  необходимости; стиль — корутины и `logging`.
- Изменения схемы БД — новой версией в `tune_loader_bot/storage/migrations.py`
  (+ `PRAGMA user_version`), идемпотентно и с копией базы. База живая, содержит
  реальные данные: сначала `--check`, потом `--apply` в окне обслуживания.
- Тесты и линт: `.venv/bin/python -m pytest`, `.venv/bin/ruff check .`; в CI — то
  же плюс порог покрытия 85%. Тесты обязаны работать в `tmp_path`: путь к базе
  берётся из настроек, а в `tests/conftest.py` стоит guard, который роняет тест
  при изменении живой `downloads.db`. Живая БД — реальные данные, писать в неё из
  тестов нельзя.
- Новый код — в пакете `tune_loader_bot/`, зависимости передаются явно
  (`Settings`, `Storage`, `MediaLibrary`, `HandlerContext`). Запуск —
  `python -m tune_loader_bot`, работа с базой — `python -m tune_loader_bot.migrate`.
- Проверка в бою — запуск `python -m tune_loader_bot` и логи. Сервис под systemd,
  не перезапускать его без явной просьбы.
- `AGENTS.md` — краткое описание, обновлять при изменениях архитектуры.
