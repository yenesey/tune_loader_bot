# tune_loader_bot — краткое описание проекта

Telegram-бот, который по ссылке скачивает аудио или видео с популярных ресурсов,
хранит файлы в локальном веб-каталоге, ведёт учёт загрузок в SQLite и умеет
автоматически следить за YouTube-плейлистами/каналами по подпискам. Дополнительно
расшифровывает голосовые сообщения в текст (OpenAI Whisper).

Появляется пакет `tune_loader_bot/` (`config`, `domain`, `storage`, CLI миграций);
легаси-модули в корне (`bot.py`, `download.py`, `database.py`, `settings.py`) пока
остаются плоскими и заменяются по фазам. Есть тесты (pytest) и линт (ruff). Идёт
поэтапный рефакторинг — план, карта дефектов и учёт прогресса в
`REFACTORING_PLAN.md` (статус фаз — §8).

## Стек

- Python 3.13 (`.venv/` в корне)
- Зависимости разделены: `requirements.txt` (рантайм), `requirements-dev.txt`
  (pytest, pytest-asyncio, pytest-cov, ruff), `requirements-whisper.txt`
  (openai-whisper + torch — тяжёлые, ставятся отдельно)
- aiogram 3.x (long polling), aiosqlite, yt-dlp (+ yt-dlp-ejs, требует Deno)
- openai-whisper + torch (ставятся отдельно: `requirements-whisper.txt`)
- ffmpeg (используется постпроцессорами yt-dlp для конвертации в mp3/mp4)

## Файлы

| Файл | Роль |
|---|---|
| `bot.py` | Тонкая точка входа: модель Whisper, адаптер транскрипции, watchdog подписок, запуск polling |
| `tune_loader_bot/media/` | `paths.py` (раскладка файлов, публичные ссылки), `ytdlp.py` (опции, загрузка, разбор плейлиста) |
| `tune_loader_bot/telegram/` | Тексты, клавиатура, подписи, доставка, обработчики, middleware, `create_dispatcher` |
| `tune_loader_bot/library.py` | Медиатека: найти готовое или скачать и сохранить (прикладной слой) |
| `download.py` | Переходный фасад над медиа-слоем: `download_media()`, `download_playlist()` |
| `tune_loader_bot/` | Пакет: `config.py`, `domain/{models,services}.py`, `media/`, `storage/{db,migrations}.py`, `migrate.py` (CLI миграций) |
| `database.py` | Переходный dict-API хранилища (`Database`); внутри — `tune_loader_bot.storage` |
| `settings.py` | Переходный dict-доступ к настройкам; в рантайме уже не используется, удаляется в фазе 8 |
| `pyproject.toml` | Конфиг ruff и pytest (проект пока не сборочный пакет) |
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
   апдейты от user_id, которых нет в `SETTINGS["users-list"]`.
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
7. **`watchdog`** (asyncio-задача, тик 60 с): для каждой подписки раз в `interval`
   секунд вытягивает плейлист (`download_playlist(url, "1,2")`), фильтрует записи по
   `keywords` (подстрока, без учёта регистра не делает), скачивает новое как аудио и
   досылает недоставленное (`delivered=0`). Ошибки логирует и продолжает цикл.
8. **Голосовые** (`voice`) — скачиваются во временный `.ogg`, распознаются Whisper'ом
   (`model.transcribe(..., language="ru")`) в отдельном потоке, ответ — `message.reply`.
9. **Запуск**: `python bot.py` из корня проекта (settings.json и downloads.db ищутся
   относительно CWD; путь к настройкам можно задать в `$TUNE_LOADER_SETTINGS`). Сейчас работает как `tune-loader-bot.service` от пользователя `denis`.

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
python bot.py                                   # локальный запуск
sudo ./install-service-systemd.sh               # установка systemd-юнита
systemctl status|restart tune-loader-bot
journalctl/tail -f tune-loader-bot.log
```

Требуется `settings.json` в CWD, `cookies.txt` для YouTube и Deno
(путь задаётся настройкой `deno-path`, иначе ищется в `PATH`).
Миграции базы — `.venv/bin/python -m tune_loader_bot.migrate --report|--check|--apply`.

## Известные особенности и подводные камни

Актуальный список дефектов (D1-D25) и статус закрытия — в `REFACTORING_PLAN.md`
(§1 и §8, там же колонка «Закрыт»). Закрыто в фазах 0-5: схема БД и миграции
(D1/D2/D8/D15), конфиг с валидацией (D17), тестовый контур и зависимости (D16),
медиа-слой (D3/D4/D6/D20/D22/D23/D24), реестр сервисов (D10/D11),
telegram-слой (D9/D21; D19/D25 закрыты частично — graceful shutdown и настройка
логов остаются в фазе 8).

Знание о сервисах — только в `tune_loader_bot/domain/services.py` (домены,
форматы, селекторы yt-dlp). Не дублировать регулярки и наборы форматов в
обработчиках.

Важно про yt-dlp: значения в `extractor_args` передаются **только списками**
(`{"youtube": {"player_js_version": ["actual"]}}`) — строку yt-dlp разбирает по
символам и пишет в лог `Invalid player JS version "a"`.

Ещё не исправлено (фазы 6-8):

- `whisper.load_model("turbo")` вызывается **на импорте** `bot.py`: старт
  медленный, ~1.4 ГБ RSS (фаза 7).
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
- Тесты и линт: `.venv/bin/python -m pytest`, `.venv/bin/ruff check .`. Тесты
  обязаны работать в `tmp_path`: `database.py` открывает `downloads.db`
  относительно CWD, а в `tests/conftest.py` стоит guard, который роняет тест при
  изменении живой БД. Живая БД — реальные данные, писать в неё из тестов нельзя.
- Проверка в бою — запуск `python bot.py` и логи. Сервис под systemd,
  не перезапускать его без явной просьбы.
- `AGENTS.md` — краткое описание, обновлять при изменениях архитектуры.
