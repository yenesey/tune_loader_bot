# tune_loader_bot — краткое описание проекта

Telegram-бот, который по ссылке скачивает аудио или видео с популярных ресурсов,
хранит файлы в локальном веб-каталоге, ведёт учёт загрузок в SQLite и умеет
автоматически следить за YouTube-плейлистами/каналами по подпискам. Дополнительно
расшифровывает голосовые сообщения в текст (OpenAI Whisper).

Проект небольшой и плоский: 4 python-модуля в корне, без пакета и без тестов.

## Стек

- Python 3.13 (`.venv/` в корне, зависимости — `requirements.txt`)
- aiogram 3.x (long polling), aiosqlite, yt-dlp (+ yt-dlp-ejs, требует Deno)
- openai-whisper + torch (установлены в `.venv`, но **в `requirements.txt` их нет**)
- ffmpeg (используется постпроцессорами yt-dlp для конвертации в mp3/mp4)

## Файлы

| Файл | Роль |
|---|---|
| `bot.py` | Точка входа: aiogram-диспетчер, middleware доступа, FSM-диалог выбора формата, обработчики сообщений/постов канала, транскрипция, фоновый `watchdog` подписок |
| `download.py` | Обёртка над yt-dlp: `download_media()` (один трек/видео) и `download_playlist()` (разбор плейлиста без скачивания) |
| `database.py` | Singleton-обёртка над SQLite (`downloads.db`): `downloads` + `subscriptions` |
| `settings.py` | Читает `settings.json` в глобальный `SETTINGS` |
| `settings.json` | **Секреты и пути** (в `.gitignore`): токен бота, po-token'ы YouTube, `download-dir`, `server-root-url`, `users-list` |
| `cookies.txt` | Cookies для yt-dlp (в `.gitignore`) |
| `downloads.db` | Живая база: ~2055 записей о загрузках (в `.gitignore`) |
| `install-service-systemd.sh` | Установка/переустановка systemd-юнита `tune-loader-bot.service` |
| `tune-loader-bot.log` | Симлинк на `/var/log/tune-loader-bot.log` |

## Как работает

1. **Доступ.** `SecurityMiddleware` (outer middleware на `dp.update`) молча отбрасывает
   апдейты от user_id, которых нет в `SETTINGS["users-list"]`.
2. **Личные сообщения** (`@dp.message`): `check_url()` определяет сервис, бот отвечает
   «Может качнем эту ☝ ссылку?» с кнопками Audio/Video (набор зависит от сервиса).
   Сообщение пользователя запоминается в глобальном `USER_DATA[user_id]`, состояние —
   `DownloadActions.select_action`.
3. **Callback** кнопки: удаляет вопрос, вызывает `process_message()` и сбрасывает состояние.
4. **Посты в канале** (`@dp.channel_post`): ссылка сразу скачивается как аудио, без диалога.
   `delivered=0` при этом остаётся — такие файлы потом досылает watchdog.
5. **`process_message`**: сначала ищет ссылку в БД (`find_url`) и отдаёт уже скачанное;
   если нет — `download()` → yt-dlp → запись в БД. Файлы ≤ 50 МБ (`TG_MAX_FILE_SIZE`)
   отправляются как audio/video с подписью-ссылками, иначе отправляется текстовая ссылка
   на `server-root-url`. Исходное сообщение пользователя удаляется.
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
   относительно CWD). Сейчас работает как `tune-loader-bot.service` от пользователя `denis`.

## Схема БД (`downloads.db`)

- `downloads(date, user_id, url, video, file_name, file_size, title, artist, channel, delivered)`
  + `UNIQUE INDEX idx_downloads(url, video, user_id)`; `artist`/`channel`/`delivered`
  добавлялись к исходной схеме вручную — в `CREATE TABLE` в коде их нет.
- `subscriptions(id, user_id, url, keywords, interval, last_check)`.
- Представление `v_users` (в коде не используется) — маппинг user_id на имена.

## Запуск и деплой

```bash
source .venv/bin/activate
python bot.py                                   # локальный запуск
sudo ./install-service-systemd.sh               # установка systemd-юнита
systemctl status|restart tune-loader-bot
journalctl/tail -f tune-loader-bot.log
```

Требуется `settings.json` в CWD, `cookies.txt` для YouTube и Deno
(путь захардкожен: `/home/denis/.deno/bin/deno` в `download.py`).

## Известные особенности и подводные камни

- `whisper.load_model("turbo")` вызывается **на импорте** `bot.py`: старт медленный,
  ~1.4 ГБ RSS (torch/whisper нет в requirements.txt).
- `YTDL_OPTS` — глобальный мутируемый dict: `download_media()` меняет `format`,
  `postprocessors`, `paths` при каждом вызове. Одновременные загрузки (пользователь +
  watchdog) могут перемешать настройки. Для плейлистов остаётся «последний» `format`.
- `USER_DATA` — обычный dict в памяти: течёт, теряется при рестарте (тогда callback
  упадёт с `KeyError`), не учитывает несколько чатов на пользователя.
- FSM-состояние общее для всех: любой текст от пользователя в состоянии выбора формата
  перезаписывает `USER_DATA`.
- `create_download_dialog`: у `soundcloud`/`yandex`/`tiktok`/`instagram` значения —
  строки вместо кортежей (`("audio")`), фильтр `btn in options[key]` работает случайно,
  за счёт подстроки.
- `find_url()` без `user_id` берёт произвольную запись (fetchone) — в watchdog
  кэш общий, а `delivered` у записи чужого пользователя.
- `save_download()` при конфликте обновляет **только** `delivered`, поэтому повторная
  загрузка не обновляет метаданные/размер.
- Regex YouTube (`youtu(?:.be/|be.com/)?`) и rutube/coub — слабо заякорены.
- Нет обработки ситуации, когда yt-dlp вернул результат, но `MoveFiles` не сработал
  (`file_path` может отсутствовать); `file_size` тогда `None`.
- Секреты (`settings.json`, `cookies.txt`, `downloads.db`, лог) в `.gitignore` —
  **не коммитить и не печатать их содержимое**.

## Соглашения для агентов

- Правки держать в стиле существующего кода: корутины, `logging`, глобальный `SETTINGS`;
  без новых зависимостей без необходимости.
- Любые изменения схемы БД делать миграцией `IF NOT EXISTS` в `Database.create()` —
  база живая, содержит реальные данные.
- Тестов нет; проверка — запуск `python bot.py` и логи. Сервис в бою под systemd,
  не перезапускать его без явной просьбы.
- `AGENTS.md` — краткое описание, обновлять при изменениях архитектуры.
