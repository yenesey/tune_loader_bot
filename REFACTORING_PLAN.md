# План рефакторинга tune_loader_bot

Документ подготовлен по фактическому коду (`bot.py`, `download.py`, `database.py`,
`settings.py`, `install-service-systemd.sh`), `AGENTS.md`, `requirements.txt` и
установленным пакетам в `.venv`.

Цели (в порядке приоритета):

1. Надёжность: убрать глобальное мутабельное состояние, гонки, падения на
   «пустой» БД, ошибки типов в `file_size`/`user_id`.
2. Тестируемость: покрыть логику тестами (pytest), для чего вынести чистые
   функции и убрать side effects с уровня импорта.
3. Модульность по смыслу: конфиг / домен-сервисы / медиа (yt-dlp) / хранилище /
   telegram-слой / фоновые задачи / транскрипция.
4. Предсказуемость эксплуатации: миграции живой БД, бэкап, логи, graceful stop.

---

## 0. Что проверено и чего проверить не удалось

Проверено по коду и по содержимому `.venv`:

- Python 3.13, aiogram **3.28.2**, aiosqlite, yt-dlp **2026.8.19** в venv, при
  этом `requirements.txt` пинит **2026.3.17** → дрейф версий.
- `openai-whisper 20250625` + torch/numpy/triton установлены в venv, но
  отсутствуют в `requirements.txt`.
- В venv **нет** pytest / pytest-asyncio / anyio → тестовый стек надо ставить.
- В aiogram 3.28.2 **нет** `aiogram.test_utils.MockedBot` → для тестов
  хендлеров нужен свой минимальный `RecordingBot` (или фейковая сессия).
- `Dispatcher.feed_update(bot, update)` есть (`aiogram/dispatcher/dispatcher.py:141`)
  → хендлеры можно тестировать без сети.
- Гит-репозиторий есть (`origin = github.com/yenesey/tune_loader_bot`, ветки
  `main`, `develop`), `.gitignore` покрывает секреты, БД, логи.
- Аргументы yt-dlp проверены по установленной версии:
  - `js_runtimes` — реальный параметр (`yt_dlp/options.py:462`);
  - корректная форма — `extractor_args = {'youtube': {'player_client': [...],
    'player_js_version': ..., 'player_js_variant': ...}}`
    (`yt_dlp/extractor/youtube/_video.py:1901, 2115, 2982, 1128`);
  - в `download.py` `"player_client": "web"` и ключи
    `youtube_player_js_version` / `youtube_player_js_variant` лежат **на уровне
    имён экстракторов** и поэтому молча игнорируются.

Проверено прогоном в рабочем shell (16.10.2026; включён файловый режим
`danger-full-access`, ограничений на команды нет):

- Python 3.13.5 — и `python3`, и `.venv/bin/python`. На момент аудита pytest в
  venv не было; в фазе 0 поставлены dev-зависимости: pytest 9.1.1,
  pytest-asyncio 1.4.0, pytest-cov 7.1.0, ruff 0.17.1.
- `requirements.txt` разошёлся с venv: aiosqlite 0.22.1 против установленного
  0.21.0, yt-dlp 2026.3.17 против установленного 2026.8.19; `openai-whisper
  20250625` и `torch 2.13.0` стоят в venv, но в requirements их нет.
- Живая БД (read-only запрос, 811 КБ): `PRAGMA user_version = 0` — учёта миграций
  нет; 2065 строк в `downloads`, 2 подписки.
  - `artist TEXT`, `channel TEXT`, `delivered BOOLEAN` **есть** — подтверждает
    ручной ALTER (в `CREATE TABLE` в коде их нет);
  - `user_id`: 1970 строк `integer`, **95 строк `NULL`** (посты канала);
  - дубликатов по `(url, video, user_id)` и по `(url, video)` — **0**;
  - `file_size IS NULL` — **0** строк;
  - `date` и `last_check` пишутся с микросекундами
    (`2025-05-10 19:03:55.219335`), поэтому текущий `strptime(..., "%f")` на этих
    данных работает.

Следствия для плана: D1 подтверждён (чистая БД не поднимется); D2 — риск
латентный (дублей нет только потому, что перед вставкой всегда вызывается
`find_url`), а путаница `str`/`int` в данных не проявилась из-за NUMERIC-affinity
колонки `STRING`; `user_version = 0` — удобная точка старта миграций; шаг
«`NULL` → `0`» из фазы 2 затрагивает 95 строк и обязателен.

---

## 1. Карта дефектов (что именно «причесываем»)

Severity: **C** — ломает данные/прод, **H** — заметный баг, **M** — хрупкость,
**L** — стиль/эксплуатация.

| # | Sev | Проблема | Где | Закрыт |
|---|---|---|---|---|
| D1 | C | `CREATE TABLE downloads` не содержит `artist`, `channel`, `delivered`, а `save_download`/`find_url` их используют → на **чистой** БД всё падает (`no such column`). Схема живёт только потому, что колонки добавлены руками (подтверждено `PRAGMA table_info` живой БД) | `database.py:33-41` vs `57-64, 67-91` | ✅ 2 |
| D2 | M | Уникальность `(url, video, user_id)` не действует для строк с `user_id IS NULL` (в SQLite NULL-ы различны; в живой БД таких строк **95 из 2065** — посты канала). Дублей сейчас нет только потому, что `process_message` всегда ищет запись перед вставкой; любой новый путь записи (или её обход) их создаст. Тип `user_id` не консистентен (`str()` в ЛС, `int` из подписки), но в данных это не проявилось: колонка `STRING` имеет NUMERIC-affinity и приводит текст к числу | `database.py:37`, `bot.py:131, 343` | ✅ 2 |
| D3 | C | Глобальный мутируемый `YTDL_OPTS`: `format`, `postprocessors`, `paths`, `playlist_items` переписываются на каждый вызов → одновременные загрузки (пользователь + watchdog) перемешивают настройки | `download.py:11-42, 73-89, 102` | ✅ 3 |
| D4 | H | `extractor_args` собран неправильно (см. §0): `player_client`, `youtube_player_js_*` не действуют; po-token'ы вложены верно, но эффект частичный | `download.py:13-24` | ✅ 3 |
| D5 | H | `outtmpl = "%(title).100s"` + `overwrites: True`: два разных видео с одинаковым названием **затирают** друг друга, а в БД обе записи указывают на один файл; имя вынесено в настройку `filename-template` (по умолчанию прежнее поведение, вариант с `%(id)s` — решение владельца) | `download.py:34, 38, 60-68` | ◐ 3 |
| D6 | H | Хук `MoveFiles`: `os.path.getsize(filepath)` без проверки существования, `os.path.basename(info.get("filepath"))` при `None` → исключение внутри хука, `result = None`, `file_size = None`; далее `result["file_size"] < TG_MAX_FILE_SIZE` → `TypeError`. Латентно: в живой БД `file_size IS NULL` — 0 строк | `download.py:58-69`, `bot.py:136` | ✅ 3 |
| D7 | H | `find_url(url, video)` без `user_id` возвращает произвольную строку (первую по индексу) → чужой `delivered`/метаданные, «кэш» не привязан к пользователю. Файл на диске не проверяется, хотя каталог ~93 ГБ и чистится | `database.py:67-91`, `bot.py:100-104` | ◐ 2 |
| D8 | H | `ON CONFLICT ... DO UPDATE SET delivered = excluded.delivered` — повторная загрузка не обновляет `file_size`/`title`/`artist`/`channel`/`date`, `None`-размер остаётся навсегда (латентно: в живой БД таких строк 0) | `database.py:61-62` | ✅ 2 |
| D9 | M | `USER_DATA` — обычный dict с объектами `Message`: течёт, теряется при рестарте (callback → `KeyError`), не учитывает несколько чатов на пользователя | `bot.py:43, 224, 234` | — |
| D10 | M | `create_download_dialog`: `("audio")` — это строка, а не кортеж, поэтому `btn in options[key]` работает как поиск подстроки; набор форматов и параметры yt-dlp описаны в двух несвязанных местах | `bot.py:186-210`, `download.py:86-89` | ✅ 4 |
| D11 | M | `check_url`: незаэкранированные точки (`www.`, `.be/`), необязательный префикс `youtu(?:...)?` → совпадёт `https://youtubeevil.com/...`; нет нормализации хоста/регистра; rutube/coub/instagram — сложные regex без тестов | `bot.py:163-182` | ✅ 4 |
| D12 | M | `datetime.strptime(..., "%Y-%m-%d %H:%M:%S.%f")` без fallback: строка без микросекунд (или записанная другим инструментом) валит весь тик watchdog; `find_url` режет `fetch[0][:10]`; `datetime.now()` без tz | `database.py:81, 105`, `bot.py:323-326` | ✅ 2 |
| D13 | M | watchdog: один `try` на весь цикл, `last_check` сохраняется **до** работы (после ошибки подписка «считается проверенной»), `playlist_items` захардкожен `"1,2"`, фильтр ключевых слов — подстрока с учётом регистра, `DB` берётся из глобала, хотя передан параметром | `bot.py:302-347` | — |
| D14 | M | `whisper.load_model("turbo")` на импорте: +1.4 ГБ RSS и медленный старт, даже если голосовые не используются; нет ограничения параллельных транскрипций; `file` может быть не определён; язык захардкожен | `bot.py:46, 251-278` | — |
| D15 | M | Singleton `Database` с `_conn: aiosqlite.Connection` как заглушкой класса, путь `"downloads.db"` захардкожен относительно CWD, нет `close()`, нет `WAL`/`busy_timeout`, `create()` можно вызвать повторно и потерять соединение | `database.py:7-45` | ✅ 2 |
| D16 | M | `requirements.txt` — полный freeze с транзитивными зависимостями, без whisper/torch и без dev-зависимостей; расходится с venv: yt-dlp 2026.3.17 vs 2026.8.19, aiosqlite 0.22.1 vs 0.21.0 | `requirements.txt` | ✅ 0 |
| D17 | M | `settings.py`: `json.load(open('settings.json'))` на импорте, без валидации и внятной ошибки, путь зависит от CWD | `settings.py:15-18` | ✅ 1 |
| D18 | L | systemd: `StandardOutput=file:` **обрезает лог** при каждом рестарте, `Description=Torrent Manager Bot`, `SyslogIdentifier=torrent-manager-bot`, запуск через `bash -c "source .venv/bin/activate"` | `install-service-systemd.sh:51,55,56,65` | — |
| D19 | L | Нет graceful shutdown: БД не закрывается, watchdog-задача не отменяется; middleware регистрируется только под `__main__`, поэтому тестируемый `dp` не равен боевому | `bot.py:350-366` | — |
| D20 | L | `enrich_info` мутирует входной dict, молча возвращает `None`, склеивает путь f-строкой; `sub_dir`/`server_url` — единственное место, где живёт правило раскладки файлов | `bot.py:75-97` | ✅ 3 |
| D21 | L | Логика доставки продублирована: `process_message` (edit_media) и `send_audio_message` (send_audio) строят одинаковые подписи `hlink("#origin")/#file` | `bot.py:124-159, 280-299` | — |
| D22 | L | Захардкоженный путь к Deno и `cookies.txt` относительно `os.getcwd()` на импорте | `download.py:25-26` | ✅ 3 |
| D23 | L | Нет `noplaylist` для одиночной ссылки: вставленный URL плейлиста/канала уедет в веерную загрузку | `download.py:91-94` | ✅ 3 |
| D24 | L | `download_playlist` возвращает `info["entries"]` без проверки `info`/`entries` → `None` превращается в `TypeError` в `scan_playlist` | `download.py:101-106`, `bot.py:302-312` | ✅ 3 |
| D25 | L | Логирование настраивается только в `__main__`; `logging.error(traceback.format_exc())` вместо `logging.exception`; `SecurityMiddleware` печатает `data.keys()` (шум + приватность) | `bot.py:68-69, 158, 347, 360-364` | — |

---

## 2. Целевая структура

```
tune_loader_bot/                # пакет (ставится/запускается как модуль)
├── __init__.py
├── __main__.py                 # python -m tune_loader_bot
├── app.py                      # composition root: config → db → media → bot/dp → jobs
├── config.py                   # Settings (dataclass/pydantic) + load_settings(path) + валидация
├── logging_setup.py            # единая настройка логов, уровень из конфига
├── domain/
│   ├── models.py               # Download, Subscription (dataclass, from_row/to_row)
│   └── services.py             # ServiceSpec-реестр: match(url), formats, ytdlp-параметры
├── storage/
│   ├── db.py                   # aiosqlite-обёртка: connect/close, PRAGMA, запросы
│   └── migrations.py           # PRAGMA user_version + шаги миграций + backup
├── media/
│   ├── paths.py                # sub_dir, file_path_for, public_url, enrich
│   └── ytdlp.py                # build_options(...) (чистая!), download(), extract_playlist()
├── telegram/
│   ├── dispatcher.py           # create_dispatcher(deps)
│   ├── middleware.py           # SecurityMiddleware
│   ├── keyboards.py            # диалог выбора формата
│   ├── texts.py                # все пользовательские строки (ru)
│   ├── captions.py             # сборка подписей/ссылок
│   ├── delivery.py             # отправка audio/video/ссылки, отметка delivered
│   └── handlers.py             # message / callback_query / channel_post
├── jobs/
│   └── watchdog.py             # SubscriptionWatcher.start()/stop()
├── transcription/
│   └── whisper_service.py      # ленивая загрузка модели, semaphore, to_thread
└── tests/                      # или корневой tests/
```

Правила, которые делают код тестируемым:

- **Никакого I/O на импорте**: конфиг читается функцией, модель Whisper — лениво,
  соединение с БД — в `app.py`, `YTDL_OPTS` не существует как глобал.
- Зависимости передаются явно (`db`, `media`, `cfg`, `bot`), а не берутся из
  глобального пространства модулей.
- `telegram/dispatcher.py:create_dispatcher(deps)` собирает `Dispatcher` целиком
  (включая middleware) → в тестах и в бою объект один и тот же.
- Слой `domain/services.py` — единственный источник правды о сервисах: он же
  кормит `check_url`, клавиатуру и параметры yt-dlp (устраняет D10/D11/D23).
- Совместимость: корневой `bot.py` остаётся тонким шимом
  (`from tune_loader_bot.app import main`), чтобы systemd-юнит не менялся.

Альтернатива «минимум телодвижений»: не заводить пакет, а разложить корень на
`bot.py`, `handlers.py`, `media.py`, `storage.py`, `services.py`, `watchdog.py`,
`transcribe.py`. Быстрее, но плоские импорты и циклические зависимости будут
мешать; рекомендую пакет.

---

## 3. Этапы работ

Каждый этап — отдельная ветка/PR, самостоятельно деплоится и не требует
перезапуска сервиса до момента выката.

### Фаза 0. Страховка (0.5 дня)

- Ветка `refactor/*` от `develop`; тег/коммит-метка базового состояния.
- `pyproject.toml`: метаданные проекта, `[tool.ruff]`, `[tool.pytest.ini_options]`
  (`asyncio_mode = "auto"`, `testpaths`, маркеры `network`, `slow`).
- `requirements.in` → `requirements.txt` (прямые зависимости: aiogram, aiosqlite,
  yt-dlp, yt-dlp-ejs; whisper/torch — extras `[whisper]`) и
  `requirements-dev.txt` (pytest, pytest-asyncio, pytest-cov, ruff).
- `.gitignore`: `downloads.db*` (WAL/SHM), `.pytest_cache/`, `.ruff_cache/`,
  `.coverage`, `htmlcov/`, `*.bak`.
- Makefile/скрипты: `make lint`, `make test`, `make run`.
- **Характеризационные тесты** на текущее поведение (см. §4) — фиксируют
  «как есть», чтобы рефакторинг было с чем сравнивать.

DoD: `ruff check` и `pytest` зелёные на нерефакторенном коде; тесты не требуют
сети, torch и секретов.

### Фаза 1. Конфиг и пути (0.5 дня)

- `config.py`: `Settings` (pydantic уже есть транзитивно через aiogram) с
  ключами `telegram-api-token`, `po-token-*`, `download-dir`,
  `server-root-url`, `users-list`, плюс новые: `db-path` (default
  `downloads.db`), `deno-path` (default `shutil.which("deno")`),
  `cookies-file`, `max-concurrent-downloads`, `whisper-model`,
  `whisper-language`, `log-level`.
- `load_settings(path)` c переменной окружения `TUNE_LOADER_SETTINGS` и понятной
  ошибкой («не найден settings.json по пути …»), кэш `get_settings()`.
- `settings.example.json` без секретов.

DoD: тесты на загрузку/валидацию/дефолты; прод-путь `./settings.json`
продолжает работать. Выполнено в фазе 1; разрешение относительных путей
(`db-path`, `cookies-file`, `deno-path`) перенесено туда, где они начинают
использоваться — фазы 2-3.

### Фаза 2. Хранилище и миграции (1.5–2 дня) — самая рискованная

- `domain/models.py`: `Download`, `Subscription` (dataclass, `from_row`,
  `as_row`, `to_legacy_dict`), канонизация типов: `user_id: int` (для постов
  канала — `0`), `video: bool`, `delivered: bool`, `file_size: int | None`;
  терпимый разбор меток времени вместо строгого `%f` (D12).
- `storage/db.py`: `Storage.open()` с `row_factory=aiosqlite.Row`,
  `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`, `close()`; путь из
  настроек; никакого singleton-`__new__` (D15).
- `storage/migrations.py`: `PRAGMA user_version = 1`, один идемпотентный набор
  шагов и для чистой, и для живой базы:
  снять `idx_downloads` (иначе `NULL → 0` упрётся в UNIQUE) → добавить
  недостающие колонки по `PRAGMA table_info` → `user_id IS NULL → 0`,
  `video`/`delivered` NULL → 0 → `delivered = MAX(delivered)` по группе и
  удаление дубликатов `(url, video, user_id)` с сохранением самой свежей записи →
  пересоздать UNIQUE-индекс → `PRAGMA integrity_check`.
  Бэкап `downloads.db.YYYYmmdd-HHMMSS.bak` делается только если таблица
  `downloads` уже существовала (свежий файл SQLite создаёт сам при `connect()`).
  Нормализации «текстовых» `user_id` нет: affinity колонки всё равно вернёт
  текст, поэтому такие строки только считаются и попадают в отчёт.
- `find_url` сортирует `ORDER BY date DESC LIMIT 1` (D7), `save_download`
  обновляет все изменяемые поля (D8) и не сбрасывает `delivered`.
- CLI: `python -m tune_loader_bot.migrate --report|--check|--apply`
  (`--report`/`--check` не меняют базу, `--apply` делает копию). Проверка файлов
  на диске — фаза 3, когда появится `media/paths.py`.

DoD: миграция прогоняется на **копии** живой БД: число строк не уменьшилось
(≥2055), дубликатов нет, `find_url`/`save_download` работают; тесты на
in-memory БД и на копии.

Факт (16.10.2026): код готов и проверен, DoD выполнен на копии — 2065 записей
сохранены, 95 `NULL user_id` → 0, `delivered` (683 записи) не потерян,
`integrity_check: ok`. Применение к живой базе — отдельный шаг с остановкой
сервиса (см. §5), статус фазы 🟡 до его выполнения.

### Фаза 3. Медиа-слой / yt-dlp (1–1.5 дня)

- `media/ytdlp.py`:
  - `build_options(spec, target_dir, cfg, *, for_playlist=False) -> dict` —
    **чистая функция**, каждый вызов возвращает новый dict (лечит D3);
  - корректный `extractor_args`: `{"youtube": {"player_client": ["web"],
    "po_token": [...], "player_js_version": ..., "player_js_variant": ...}}`,
    `js_runtimes` из конфига (лечит D4/D22);
  - `retries`, `fragment_retries`, `socket_timeout`, `extractor_retries`,
    `concurrent_fragment_downloads`, `noprogress`, `noplaylist` для одиночного
    медиа (D23);
  - сбор результата через хук-класс с состоянием: проверка
    `info.get("filepath")`, `os.path.isfile`, fallback на `prepare_filename`;
    при отсутствии файла — понятный лог и `None` вместо исключения (D6);
  - политика имён: добавить `%(id)s` в `outtmpl` для **новых** загрузок
    (старые имена в БД не трогаем) + `restrictfilenames`/санитизация (D5);
  - `extract_playlist()` на «лёгком» наборе опций (`skip_download`,
    `extract_flat="in_playlist"`, без постпроцессоров), терпимость к
    `info is None` и `entries is None` → `[]` (D24);
  - обработка ошибок: `DownloadError` + `OSError`/`PostProcessingError`,
    логирование с URL и длительностью;
  - инъекция фабрики `ydl_factory` — тесты подменяют `YoutubeDL` без сети.
- `media/paths.py`: `sub_dir`, `file_path_for`, `public_url` (корректный
  `quote(..., safe="")`), `enrich` без мутации входа (D20).
- Конкурентность: `asyncio.Semaphore(cfg.max_concurrent_downloads)` и
  per-URL `asyncio.Lock` — два одновременных запроса одной ссылки не качают
  файл дважды.

DoD: тесты фабрики опций (изоляция словарей при конкурентных вызовах, разные
наборы для audio/video/playlist), тесты хука (файл есть / файла нет / `filepath`
= None), тест «`DownloadError` → None и запись в лог».

### Фаза 4. Реестр сервисов (0.5 дня)

- `domain/services.py`: `ServiceSpec(name, patterns, formats, audio_format,
  video_format, need_js_runtime, need_cookies)`; матчинг через
  `urllib.parse.urlsplit` + нормализованный хост + точные хосты/пути, набор
  негативных кейсов в тестах (D10/D11).
- `check_url` и клавиатура строятся из реестра; `formats` — множество, а не
  строка-подстрока.

DoD: golden-таблица URL (~40 кейсов, включая `youtu.be`, `music.youtube.com`,
`youtubeevil.com`, `m.youtube.com`, `vt.tiktok.com`, `instagram.com/reel/...`,
мусор в query/фрагменте, верхний регистр хоста).

Факт (16.10.2026): сделано; в реестре 7 сервисов, матчинг по `urlsplit` + точным
доменам (поддомены разрешены, «похожие» чужие домены — нет). Дополнительно
проверено на реальных данных: все **2050** уникальных ссылок из живой базы
получают тот же вердикт, что и старый `check_url` (0 регрессий), а ложные
срабатывания (`youtubeevil.com`, `soundcloud.evil.example`) закрыты.

### Фаза 5. Telegram-слой (1.5 дня)

- `telegram/texts.py`, `keyboards.py`, `captions.py`, `delivery.py`, `handlers.py`,
  `middleware.py`, `dispatcher.py:create_dispatcher(deps)`.
- Состояние диалога — в FSM (`state.update_data(chat_id, message_id, url)`) вместо
  `USER_DATA`; callback без данных отвечает «ссылка устарела, пришлите снова»
  вместо `KeyError` (D9).
- Проверка перед отправкой: существует ли `file_path` и не `None` ли
  `file_size`; при отсутствии файла — повторная загрузка/честная ошибка (D6/D7).
- Единая точка доставки и единый конструктор подписи (D21); удаление исходного
  сообщения — best-effort с логом.
- Middleware регистрируется в фабрике (D19), логи без дампа `data.keys()` (D25).

DoD: тесты через `dp.feed_update(recording_bot, Update(...))`: чужой user_id
отбрасывается, ЛС со ссылкой → клавиатура, callback → вызов доставки (с фейковым
media), устаревший callback не падает, channel_post скачивается как audio.

### Фаза 6. Watchdog (0.5–1 день)

- `jobs/watchdog.py`: класс с `start()/stop()`, цикл по монотонным часам,
  `stop()` отменяет задачу; инъекция `clock`, `media`, `db`, `bot`, `cfg`.
- `last_check` двигается **после** успешной проверки; ошибка подписки не
  прерывает остальные (per-subscription `try/except` + `logging.exception`).
- `playlist_items` и число проверяемых записей — из конфига; фильтр ключевых
  слов: регистронезависимо, разделители `,`, поддержка исключений через `-` (D13).

DoD: тесты «подписка не созрела/созрела», «новый элемент скачан и доставлен»,
«`delivered=0` → повторная отправка», «ошибка первой подписки не мешает второй»,
«`last_check` не сдвинулся при ошибке».

### Фаза 7. Транскрипция (0.5 дня)

- `transcription/whisper_service.py`: ленивая загрузка модели (первый вызов,
  а не импорт), `asyncio.to_thread`, `Semaphore(1)`, модель/язык из конфига,
  опциональность зависимости (нет whisper → фича выключена с внятным логом),
  `TemporaryDirectory` вместо `delete=False`, проверка размера/длительности (D14).

DoD: тест с фейковым транскрибером; старт приложения без загрузки torch;
замер «до/после» времени старта и RSS зафиксирован в PR.

### Фаза 8. Композиция, эксплуатация, документация (1 день)

- `app.py`: сборка зависимостей, `on_startup/on_shutdown` (закрыть БД, остановить
  watchdog, закрыть сессию бота), корректный SIGTERM; `bot.py` — шим.
- `logging_setup.py`: формат/уровень из конфига, `logging.exception`.
- `install-service-systemd.sh`: `StandardOutput=append:`, осмысленные
  `Description`/`SyslogIdentifier`, `Environment=PYTHONUNBUFFERED=1`,
  `ExecStart=.venv/bin/python -m tune_loader_bot` (без `bash -c`), проверка прав
  на `/var/log`.
- GitHub Actions: `ruff check`, `pytest` (без network-маркера) на Python 3.13.
- Обновить `AGENTS.md` (новая структура, команды, миграции) и `README.md`.

DoD: CI зелёный, `python -m tune_loader_bot` поднимается на копии окружения,
`systemctl`-юнит не сломан.

**Суммарно: ~8–10 рабочих дней.** Критический путь — фазы 0→2→3 (данные и
медиа); фазы 4–8 можно параллелить/делать инкрементально.

---

## 4. Тестовая стратегия

Инструменты: `pytest`, `pytest-asyncio` (`asyncio_mode=auto`), `pytest-cov`,
`ruff`. Сеть, yt-dlp, ffmpeg, torch — **вне** юнит-тестов (всё через фейки).

Раскладка:

```
tests/
├── conftest.py            # фикстуры: settings, tmp settings.json, db, recording_bot, fake_ydl, clock
├── test_config.py
├── test_paths.py
├── test_services_registry.py
├── test_db.py             # in-memory + миграции
├── test_db_migration_live.py  # на КОПИИ downloads.db, skip если файла нет
├── test_ytdlp_options.py
├── test_ytdlp_download.py
├── test_watchdog.py
├── test_handlers.py
└── test_whisper_service.py
```

Ключевые фикстуры:

- `settings` — `Settings` с `tmp_path` в качестве `download-dir` и `db-path`;
  `settings.json` пишется в `tmp_path` (секреты в тестах фиктивные).
- `db` — `tmp_path/"test.db"`, миграции применены; никаких обращений к живой БД.
- `live_db_copy` — `shutil.copy2("downloads.db", tmp_path)` (skip, если файла
  нет); проверяем: строки не потеряны, дубликатов нет, все строки читаются.
- `recording_bot` — наследник `aiogram.Bot` с подменённой сессией, копит вызовы
  `send_audio`/`send_message`/`edit_media` (готового `MockedBot` в 3.28.2 нет).
- `fake_ydl` — класс с `download()`/`extract_info()`, который вызывает
  postprocessor-хук с подготовленным `info_dict` и создаёт файл в `tmp_path`.
- `clock` — вызываемый объект времени, чтобы тестировать интервалы подписок без
  `sleep`.

Покрытие (порог в CI):

- overall `--cov-fail-under=75`, целевое 85;
- 90%+ для `storage/`, `media/paths.py`, `media/ytdlp.py`, `domain/`,
  `jobs/watchdog.py` (там вся логика и все бывшие баги);
- `transcription/`, сетевые обёртки — исключены из гейта.

Что именно закрывают тесты (регрессии на найденные дефекты):

- D1: `create()` на пустом файле + `save_download` + `find_url` — без ошибок;
- D2: пост канала с `user_id=None` дважды → одна строка; `str`/`int` user_id →
  одна строка после канонизации;
- D3: два конкурентных `build_options` → независимые dict; опции audio не
  протекают в playlist;
- D4: снимок-тест собранных `extractor_args` (структура вложенности);
- D5: два разных видео с одинаковым `title` → разные файлы;
- D6: хук без файла → `None` + лог, а не исключение; `file_size=None` → ветка
  «отправить ссылку»;
- D7: `find_url` не возвращает чужую строку; отсутствующий файл → перезагрузка;
- D8: повторный `save_download` обновляет метаданные/размер;
- D9: callback после «рестарта» (пустой FSM) не падает;
- D10/D11: golden-таблица URL и наборов форматов;
- D12: `last_check` без микросекунд парсится; битая дата не убивает тик;
- D13: `last_check` не сдвигается при ошибке; ошибка одной подписки не мешает
  другим;
- D14: сервис транскрипции без whisper → фича выключена;
- D24: `extract_info` → `None`/`entries=None` → `[]`.

Интеграционные (маркер `network`, по умолчанию выключены, запуск вручную):
реальный `yt-dlp --simulate` по 1–2 стабильным ссылкам + проверка, что Deno и
cookies подхватываются; прогон `python -m tune_loader_bot` на копии окружения и
ручной smoke (ссылка → audio, ссылка → video, пост канала, голосовое,
досылка `delivered=0`).

---

## 5. Работа с живой БД (порядок и безопасность)

1. Бэкап: `sqlite3 downloads.db ".backup 'downloads.db.bak-YYYYmmdd'"` (вне
   репозитория) — до любых изменений.
2. Остановить сервис только на время миграции (окно согласовать с
   пользователем; сами правки кода сервис не трогают).
3. `python -m tune_loader_bot.migrate --check` (read-only отчёт), затем
   `--apply` (пишет бэкап-копию рядом с БД и повышает `user_version`).
4. `python -m tune_loader_bot.migrate --report` — сверить количество строк и
   отсутствие дубликатов/NULL user_id.
5. Запуск сервиса, проверка логов и одной реальной ссылки.
6. Откат: восстановить `.bak`, вернуть предыдущий коммит.
7. Тесты и любые скрипты обязаны работать в `tmp_path`: `database.py` открывает
   `downloads.db` **относительно CWD**. В `tests/conftest.py` стоит autouse-guard,
   который роняет тест при изменении живой БД (инцидент 16.10.2026, см. журнал §8).

Никаких «миграций на старте без бэкапа» для живой БД; для чистой/тестовой —
автоматически.

---

## 6. Риски и как их снижаем

| Риск | Митигация |
|---|---|
| Порча живой БД (2055 записей) | тесты на копии, бэкап, `--check` перед `--apply`, миграции идемпотентны |
| Регресс в бою (systemd, без CI-деплоя) | каждая фаза отдельным PR, `bot.py`-шим, ручной smoke-чеклист перед выкатом |
| yt-dlp меняет API/аргументы | версия пинится, снимок-тесты на собранные опции, ручная сверка `-v` после апгрейда |
| Whisper/torch тяжесть | ленивый импорт, extras `[whisper]`, опция выключения |
| Долгий рефакторинг «всё сразу» | фазы shippable по отдельности; фазы 1–4 дают максимум эффекта при минимуме риска |
| Изменение имён файлов на диске | `[id]` в `outtmpl` только для новых загрузок; старые строки не мигрируем |

Non-goals: смена СУБД, веб-интерфейс, изменение пользовательских текстов и
вёрстки сообщений (кроме выноса в `texts.py`), полная перераскладка уже
скачанных 93 ГБ, Redis/очереди.

---

## 7. Открытые вопросы к владельцу

1. **Транскрипция**: оставляем Whisper в основном процессе (ленивая загрузка) или
   выносим в отдельный опциональный сервис? Устанавливать ли whisper/torch в
   `requirements` как extras?
2. **Имена новых файлов**: можно ли добавить `[id]` в шаблон имени для новых
   загрузок, чтобы исключить затирание одноимённых видео?
3. **Процесс**: feature-ветки в этот же репозиторий с PR в `develop`? Нужен ли CI
   на GitHub Actions?
4. **Несколько чатов на пользователя**: нужно ли поддерживать (иначе FSM по
   `chat_id` достаточно)?
5. **Окно миграции**: можно ли один раз остановить сервис для бэкапа/миграции БД?
6. **Порядок работ**: начинать с фаз 0–2 (данные/БД, максимум пользы) или сразу
   с разбиения на модули (фазы 1, 4, 5)?

---

## 8. Учёт прогресса (где хранятся отметки)

Источник правды — **репозиторий**, а не сессия агента. Сессионные списки задач
(`todo_write`) живут в логе сессии: они переживают сжатие контекста и возврат в
ту же сессию, но исчезают в новой сессии и не видны в git. Поэтому отметки
ведутся здесь, в этом файле, и в git-истории.

Правила:

1. **Этот файл.** Статус фазы в таблице ниже + запись в журнале. Отметка ставится
   только вместе с подтверждением: тесты/линт зелёные, шаг DoD выполнен.
2. **Git.** Одна фаза = одна ветка `refactor/<phase>` = один PR с чек-листом DoD
   в описании. Коммиты в формате `refactor(storage): убрать NULL user_id (план D2)` —
   ссылка на номер дефекта из §1. После мержа в `develop` — отметка в таблице.
3. **Чек-листы в PR** дублируют DoD фазы; в файле остаётся итог (ссылка на PR).
4. **`AGENTS.md`** обновляется только при изменении архитектуры/команд запуска,
   это не журнал.
5. **Сессионный `todo_write`** — только «что делаю прямо сейчас» в рамках текущей
   фазы (видно в GUI, помогает не потерять шаг при длинной работе).
6. Если работа идёт автономными раундами без человека — цель фиксируется через
   goal-механизм, но по-прежнему с записью итога в таблицу/журнал.

Статусы: `⬜ не начата` · `🔄 в работе` · `🟡 ждёт ревью` · `✅ готово` ·
`⛔ заблокирована`.

| Фаза | Статус | Ветка / PR | Проверка | Дата |
|---|---|---|---|---|
| 0. Страховка (git, pyproject, requirements, характеризационные тесты) | ✅ | `refactor/phase-0` | 55 тестов, ruff clean, живая БД не тронута | 2026-10-16 |
| 1. Конфиг (Settings, валидация, env) | ✅ | `refactor/phase-1` | 70 тестов, ruff clean, живой settings.json валиден | 2026-10-16 |
| 2. Хранилище и миграции | 🟡 | `refactor/phase-2` | 115 тестов; миграция прогнана на копии живой БД (2065 записей, integrity ok); сама живая БД ждёт окна | 2026-10-16 |
| 3. Медиа-слой / yt-dlp | ✅ | `refactor/phase-3` | 159 тестов, ruff clean; опции и скачивание проверены на живом yt-dlp (mp3 4.9 МБ в /tmp) | 2026-10-16 |
| 4. Реестр сервисов | ✅ | `refactor/phase-4` | 231 тест, ruff clean; сверка со старым кодом на 2050 реальных ссылках — 0 регрессий | 2026-10-16 |
| 5. Telegram-слой | ⬜ | — | — | — |
| 6. Watchdog | ⬜ | — | — | — |
| 7. Транскрипция | ⬜ | — | — | — |
| 8. Композиция, эксплуатация, документация | ⬜ | — | — | — |

Дефекты из §1 закрываются по мере фаз; отметка о закрытии ставится в таблице
дефектов (колонка «Закрыт в фазе» добавляется по факту первого закрытия).

### Журнал (append-only, новые записи внизу)

Формат: `YYYY-MM-DD · фаза · что сделано · проверка · коммит/PR`

- 2026-10-16 · — · подготовлен план рефакторинга (без правок кода) · `git diff`
  пуст, план добавлен файлом `REFACTORING_PLAN.md` · коммит `3e1ba97`
- 2026-10-16 · — · сверены окружение и живая БД: python 3.13.5, pytest в venv нет,
  дрейф requirements (yt-dlp, aiosqlite), `user_version=0`, 2065 записей,
  95 `NULL user_id`, дублей и `NULL`-размеров нет; вопрос про sandbox снят ·
  read-only запросы к `downloads.db`, `.venv/bin/pip list`, `git status` ·
  коммит `03a4dcf`
- 2026-10-16 · 0 · ⚠️ инцидент: первый прогон тестов не был изолирован по CWD и
  записал в живую БД 1 строку в `downloads` и 1 в `subscriptions`; замечено
  сразу, бэкап `downloads.db.damage-20261016-183155.bak`, мусор удалён по
  содержимому строк, реальные подписки (id 2 `@etozbignev`, id 4 `@SuperSharij`)
  не пострадали, счётчики восстановлены (2065 / 2); в `tests/conftest.py` добавлен
  guard, роняющий тест при изменении живой БД · проверка содержимым строк +
  read-only подсчёт · —
- 2026-10-16 · 0 · фаза 0: `pyproject.toml` (ruff + pytest + coverage),
  requirements разделены на runtime/dev/whisper по факту venv, `.gitignore`
  (+`.venv/`, WAL, кэши, бэкапы), характеризационные тесты · 55 passed, ruff
  clean, mtime/size живой БД не изменились · коммит `bb55f0b`, merge `7caee17`
- 2026-10-16 · 1 · фаза 1: `config.py` — `Settings` на pydantic (alias'ы старых
  ключей, дефолты, `ConfigError`, `$TUNE_LOADER_SETTINGS`), `settings.py` стал
  dict-шимом, `settings.example.json`; живой `settings.json` прогнан валидацией
  (7 users, po-token'ы заданы) · 70 passed, ruff clean · коммит `e251435`,
  merge `a4dc969`
- 2026-10-16 · 2 · фаза 2: пакет `tune_loader_bot/` (переехал `config.py`),
  `domain/models.py`, `storage/db.py`, `storage/migrations.py`, CLI `migrate`;
  `database.py` — dict-шим над `Storage`. Закрыты D1/D2/D8/D12/D15 · 115 passed,
  ruff clean; CLI на копии живой БД: 2065 → 2065, 95 `NULL` → 0, `delivered` 683
  сохранён, integrity ok · коммит `001df3d` (лёг в `develop` линейно, ветка
  `refactor/phase-2` указывает на него)
- 2026-10-16 · 2 · применение к живой БД: ожидает окна обслуживания (нужен sudo
  на `systemctl`); `--check` на живой базе: 0 → 1, 95 `NULL` → 0, 2065 записей;
  при любом рестарте сервиса миграция выполнится автоматически с бэкапом · —
- 2026-10-16 · 3 · фаза 3: `media/paths.py` и `media/ytdlp.py`; `download.py` —
  фасад над медиа-слоем, `bot.py` использует общие `sub_dir`/`enrich_info`.
  Закрыты D3/D4/D6/D20/D22/D23/D24; D5 переведён в настройку `filename-template`.
  Проверка на живом yt-dlp нашла ошибку вне тестов: значения `extractor_args`
  обязаны быть списками, иначе yt-dlp разбирает строку по символам
  (`Invalid player JS version "a"`) — исправлено, добавлен структурный тест.
  Реальное скачивание через `download.download_media`: ExtractAudio → MoveFiles →
  mp3 4.9 МБ в `/tmp` (удалён) · 159 passed, ruff clean, живая БД без изменений ·
  коммит `9763167`, merge `0d4ce43`
- 2026-10-16 · 3 · добавлены тесты CLI миграций (14 штук: `--report`/`--check`/
  `--apply`, JSON, отсутствующая база, «новее схемы», путь из настроек) — CLI это
  инструмент применения миграции к живой базе, его нельзя оставлять без проверки ·
  173 passed, ruff clean, `migrate.py` покрыт · коммит `c6266e6`+
- 2026-10-16 · 4 · фаза 4: `domain/services.py` — реестр сервисов
  (`ServiceSpec`: домены, форматы-`frozenset`, селектор формата, путь/схема);
  `bot.check_url` и `create_download_dialog` берут данные из реестра,
  `media/ytdlp.py` — селектор формата оттуда же. Закрыты D10 (строки `("audio")`
  → `frozenset` и порядок кнопок) и D11 (матчинг по `urlsplit`/точным доменам:
  `youtubeevil.com` и `soundcloud.evil.example` больше не проходят; побочно
  распознаются `http://`, `m.youtube.com`, `snd.sc`). Сверка со старым `check_url`
  на 2050 реальных ссылках из живой базы: 0 регрессий · 231 passed, ruff clean,
  живая БД без изменений · коммит `f0d4fbe`, merge `b3be61a`
