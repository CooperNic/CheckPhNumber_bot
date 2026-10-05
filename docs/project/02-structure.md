# Структура репозитория и поток данных

[← Оглавление](project-docs.md)

## Структура

```
checkphnumber_bot/
├── main.py                  # точка входа: asyncio.run(checkph.bot.main())
├── checkph/
│   ├── schema.sql           # DDL трёх слоёв + sync_state + представления
│   ├── db.py                # connect(), init_schema(), transaction()
│   ├── csv_reader.py        # чтение снимка (каталог или zip), валидация
│   ├── importer.py          # diff-импорт снимка (SCD2), CLI --dir/--backfill
│   ├── sync.py              # проверка портала, скачивание, импорт, zip-архив
│   ├── lookup.py            # normalize(), resolve(), history()
│   ├── enrichment.py        # record_owner_observation(), record_spam_observation()
│   └── bot.py               # aiogram-обработчики, Repository, access.log
├── tests/                   # pytest на временной базе с мини-CSV
├── getcsv.sh                # обёртка: python -m checkph.sync
├── deploy/
│   ├── checkph-sync.service # systemd oneshot
│   └── checkph-sync.timer   # каждые 6 часов
├── requirements.txt, requirements-dev.txt
├── .env.example
├── agents.md                # краткое описание проекта
├── docs/
│   ├── project-idea.md      # продуктовая идея
│   └── project/             # техническая документация (этот каталог)
├── inCSV/                   # не в git
│   ├── *.csv                # копия последнего снимка (несжатая)
│   └── archive/YYYYMMDD.zip # история снимков (zip, CSV в корне)
└── db.sqlite/               # не в git
    ├── registry.db          # рабочая база (~85 МБ после пяти снимков)
    └── numeration_registry.db  # старая база (134 ГБ), не используется, удалить вручную
```

## Модули `checkph/`

| Модуль | Ответственность | Подробнее |
|---|---|---|
| `db.py` | соединение SQLite (autocommit, WAL, `busy_timeout`, `foreign_keys`), `init_schema()`, контекст `transaction()` | [04-database.md](04-database.md) |
| `csv_reader.py` | чтение каталога или zip снимка, валидация строк, запрет пересечений, упаковка zip | [03-csv-format.md](03-csv-format.md) |
| `importer.py` | diff-импорт в версионируемую таблицу, CLI | [05-import.md](05-import.md) |
| `sync.py` | листинг портала, скачивание, импорт, архив zip, курсор `sync_state` | [05-import.md](05-import.md) |
| `lookup.py` | нормализация ввода, поиск по реестру, история номера | [06-lookup.md](06-lookup.md) |
| `enrichment.py` | запись наблюдений внешних источников | [04-database.md](04-database.md) |
| `bot.py` | Telegram-обработчики, журнал запросов | [07-bot.md](07-bot.md) |

## Поток данных

```
opendata.digital.gov.ru/downloads/
        │  python -m checkph.sync  (или ./getcsv.sh / systemd timer)
        │  индекс → при новой дате скачать 4 CSV → валидация → import_snapshot
        │  → inCSV/archive/YYYYMMDD.zip (+ копия CSV в inCSV/)
        ▼
db.sqlite/registry.db
        │  checkph.lookup.resolve(): индексный поиск диапазона по (code, from_number)
        ▼
ответ в Telegram: оператор (регион) [+ владелец, спам — если есть наблюдения]
```

Синхронизация реестра и работа бота — два отдельных процесса. Бот при старте вызывает `init_schema()`, поэтому пустая база создаётся автоматически, но без импорта все номера будут «не найдены».
