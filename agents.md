# CheckPhNumber bot

Техническое описание репозитория для разработки. Продуктовая идея — в `docs/project-idea.md`.

Бот отвечает в Telegram, какому оператору и региону выделен номер, по открытому реестру российской системы и плана нумерации. Реестр хранится в SQLite диапазонами с историей версий; поиск — индексный, по диапазону. Сбор базы и работа бота — два отдельных запуска.

## Стек

| Компонент | Версия / инструмент |
|---|---|
| Язык | Python 3.12 (интерпретатор окружения: 3.12.13) |
| Окружение | `.venv` в корне, создаётся как `python3.12 -m venv .venv` |
| Telegram | aiogram 3.31, long polling |
| Конфигурация | python-dotenv, файл `.env` |
| Хранилище | SQLite через стандартный `sqlite3`, WAL |
| Загрузка исходных данных | bash + `wget` (`getcsv.sh`) |
| Тесты | pytest (`requirements-dev.txt`) |
| Линтер, Docker, CI | нет |

Системный `python3` на машине — 3.10.12. Для проекта нужен интерпретатор `.venv` или явный `python3.12`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Секреты и настройки: `.env` (шаблон — `.env.example`): `API_TOKEN` обязателен, `DB_PATH` (по умолчанию `db.sqlite/registry.db`) и `ACCESS_LOG` (по умолчанию `access.log`) — необязательны. Каталоги `.venv/`, `db.sqlite/`, `inCSV/` и файл `access.log` в `.gitignore`.

## Структура

```
checkphnumber_bot/
├── main.py                  # точка входа: asyncio.run(checkph.bot.main())
├── checkph/
│   ├── schema.sql           # DDL трёх слоёв + представления (идемпотентно)
│   ├── db.py                # connect(), init_schema(), transaction()
│   ├── csv_reader.py        # чтение снимка CSV, валидация, проверка пересечений
│   ├── importer.py          # diff-импорт снимка (SCD2), CLI --dir/--backfill
│   ├── lookup.py            # normalize(), resolve(), history()
│   ├── enrichment.py        # record_owner_observation(), record_spam_observation()
│   └── bot.py               # aiogram-обработчики, Repository, access.log
├── tests/                   # pytest на временной базе с мини-CSV
├── getcsv.sh                # скачивание снимка в inCSV/archive/YYYYMMDD/
├── requirements.txt, requirements-dev.txt
├── .env.example
├── inCSV/                   # не в git
│   ├── *.csv                # копия последнего снимка
│   └── archive/YYYYMMDD/    # снимки по датам — источник для импорта
├── db.sqlite/
│   ├── registry.db          # рабочая база (~85 МБ после пяти снимков)
│   └── numeration_registry.db  # старая база (134 ГБ), не используется, удалить вручную
└── docs/project-idea.md
```

Python-скрипты считают пути относительными к текущему каталогу. Их запускают из корня репозитория.

## Поток данных

```
opendata.digital.gov.ru
        │  getcsv.sh → inCSV/archive/YYYYMMDD/*.csv (+ копия в inCSV/)
        ▼
python -m checkph.importer --backfill
        │  каждый каталог archive/YYYYMMDD по возрастанию даты;
        │  diff с текущим состоянием → закрыть/открыть версии диапазонов
        ▼
db.sqlite/registry.db
        │  checkph.lookup.resolve(): индексный поиск диапазона по (code, from_number)
        ▼
ответ в Telegram: оператор (регион) [+ владелец, спам — если есть наблюдения]
```

Источник файлов:

- `https://opendata.digital.gov.ru/downloads/ABC-3xx.csv`
- `https://opendata.digital.gov.ru/downloads/ABC-4xx.csv`
- `https://opendata.digital.gov.ru/downloads/ABC-8xx.csv`
- `https://opendata.digital.gov.ru/downloads/DEF-9xx.csv`

## Формат CSV

Кодировка UTF-8, BOM у заголовка есть не всегда (читается через `utf-8-sig`), разделитель `;`, первая строка — заголовок. Поля:

| Индекс | Колонка CSV | Смысл |
|---|---|---|
| 0 | АВС/ DEF | код ABC или DEF, 3 цифры |
| 1 | От | начало 7-значного хвоста диапазона |
| 2 | До | конец хвоста, включительно |
| 3 | Емкость | размер диапазона; всегда равен `До − От + 1` |
| 4 | Оператор | наименование оператора; написание меняется от снимка к снимку |
| 5 | Регион | регион; несколько значений через `\|`; с выгрузки 2026‑10‑04 у части строк стоит `-` |
| 6 | Территория ГАР | территория по ГАР, тоже может содержать `\|`, в ABC-8xx — запятые |
| 7 | ИНН | ИНН оператора; одна строка в реестре с пустым ИНН |

Разбор — `csv.reader`. `csv_reader.read_snapshot()` отклоняет снимок (`SnapshotError`), если поле не парсится или диапазоны одного кода пересекаются.

## База

Файл: `db.sqlite/registry.db`. Схема — `checkph/schema.sql`, три слоя.

Слой 1, реестр:

| Таблица | Назначение |
|---|---|
| `import_batch` | снимок: `source_date` (уникальна), `files_sha256`, `range_count` |
| `operator` | оператор; ключ — `inn` (уникален, может быть NULL — тогда ключ по `name`); `name` — самое частое написание в последнем снимке |
| `operator_name_variant` | все встреченные написания с `first_seen_batch`/`last_seen_batch` |
| `number_range` | версия диапазона: `code`, `from_number`, `to_number`, `operator_id`, `region_raw`, `gar_raw`, `valid_from_batch`, `valid_to_batch` (NULL = действует) |

Индексы: `ix_range_current` — частичный уникальный `(code, from_number) WHERE valid_to_batch IS NULL` для поиска; `ix_range_all (code, from_number, to_number)` для истории.

Слой 2: `phone_number(number PK, created_at)` — строка создаётся лениво, при первом наблюдении.

Слой 3: `data_source`, `number_owner_observation`, `number_spam_observation` (append-only), `lookup_event` (журнал запросов к боту). Представления `v_number_owner_current` (последнее наблюдение владельца) и `v_number_spam_current` (есть спам-метка за 180 дней).

Текущее наполнение после `--backfill` по пяти снимкам (2024‑07‑24, 08‑09, 09‑13, 11‑11, 2026‑10‑04): 551 158 версий диапазонов, из них 451 099 открытых; 1 416 операторов, 2 298 вариантов написаний. Импорт всех пяти снимков — около 30 с.

Дата снимка `archive/20240724` взята из имени каталога по решению владельца проекта; по содержимому он новее `20241111`, поэтому у части диапазонов история содержит переходы `2024‑07‑24 → 2024‑08‑09`, которые в реестре шли в обратную сторону.

## Импорт

```bash
.venv/bin/python -m checkph.importer --backfill              # все inCSV/archive/YYYYMMDD по порядку
.venv/bin/python -m checkph.importer --dir inCSV/archive/20261004   # один снимок, дата из имени
.venv/bin/python -m checkph.importer --dir /path --date 2026-10-04  # дата явно
```

Правила diff по ключу `(code, from_number, to_number)`: диапазон исчез — закрыть версию; появился — открыть; изменились `operator_id`/`region_raw`/`gar_raw` — закрыть и открыть; совпадает — ничего. Смена написания имени без смены ИНН версий не создаёт. Уже импортированная дата пропускается; снимок с датой не новее последней импортированной отклоняется.

## Поиск

`lookup.normalize(text)` убирает пробелы, дефисы, скобки, плюс; принимает 11 цифр с ведущей 7 (8 заменяется на 7), иначе `None`. `lookup.resolve(con, number)` берёт ближайший снизу открытый диапазон кода и проверяет верхнюю границу; затем двумя отдельными запросами читает представления слоя 3. Около 20–25 мкс на запрос. `Attribution.display_region` возвращает `region`, а при `-`/пустом — `gar`. `lookup.history(con, number)` — все версии диапазонов, накрывавших номер, с датами `since`/`until`.

## Бот

`checkph.bot.main()`: читает `.env`, открывает базу (`init_schema` идемпотентен), создаёт `Repository` — обёртку над одним соединением с `threading.Lock`; обращения к базе идут через `asyncio.to_thread`.

- `/start` — текст с форматом ввода.
- любое другое сообщение — `normalize` → при ошибке формата подсказка; иначе `resolve` → `operator (регион)` плюс строки про владельца и спам при наличии наблюдений; иначе `Номер не найден.`
- каждый запрос пишется в `lookup_event` и в `access.log` (`исходный ввод|номер|оператор|регион|имя|@username|id|секунды`).

Запуск из корня: `.venv/bin/python main.py`.

## Тесты

```bash
.venv/bin/python -m pytest -q
```

Тесты создают базу во временном каталоге и мини-снимки CSV через `tests/conftest.py::write_snapshot`. Реальные данные в `inCSV/` не используются.

## Известные ограничения

- `region_raw`/`gar_raw` хранятся строкой как в выгрузке; запрос «все диапазоны региона» потребует разбиения на справочник (вне текущего этапа).
- Интеграций с внешними источниками (банки, спам-базы) нет — только API записи наблюдений в `enrichment.py`.
- `owner_name` — персональные данные; перед подключением источников нужно основание обработки по 152‑ФЗ и срок хранения.
- `lookup_event` и `access.log` содержат номера и идентификаторы Telegram.
