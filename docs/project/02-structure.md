# Структура репозитория и поток данных

[← Оглавление](project-docs.md)

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
├── agents.md                # краткое описание проекта
├── docs/
│   ├── project-idea.md      # продуктовая идея
│   └── project/             # техническая документация (этот каталог)
├── inCSV/                   # не в git
│   ├── *.csv                # копия последнего снимка
│   └── archive/YYYYMMDD/    # снимки по датам — источник для импорта
└── db.sqlite/               # не в git
    ├── registry.db          # рабочая база (~85 МБ после пяти снимков)
    └── numeration_registry.db  # старая база (134 ГБ), не используется, удалить вручную
```

## Модули `checkph/`

| Модуль | Ответственность | Подробнее |
|---|---|---|
| `db.py` | соединение SQLite (autocommit, WAL, `foreign_keys`), `init_schema()`, контекст `transaction()` | [04-database.md](04-database.md) |
| `csv_reader.py` | чтение каталога снимка, валидация строк, запрет пересечений | [03-csv-format.md](03-csv-format.md) |
| `importer.py` | diff-импорт в версионируемую таблицу, CLI | [05-import.md](05-import.md) |
| `lookup.py` | нормализация ввода, поиск по реестру, история номера | [06-lookup.md](06-lookup.md) |
| `enrichment.py` | запись наблюдений внешних источников | [04-database.md](04-database.md) |
| `bot.py` | Telegram-обработчики, журнал запросов | [07-bot.md](07-bot.md) |

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

Сбор базы и работа бота — два отдельных запуска, общего процесса у них нет. Бот при старте вызывает `init_schema()`, поэтому пустая база создаётся автоматически, но без импорта все номера будут «не найдены».
