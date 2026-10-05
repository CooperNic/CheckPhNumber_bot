# Документация проекта CheckPhNumber bot

Техническое описание репозитория для разработки. Продуктовая идея — в [../project-idea.md](../project-idea.md).

| Раздел | Содержание |
|---|---|
| [01-stack-and-setup.md](01-stack-and-setup.md) | Стек, окружение `.venv`, установка зависимостей, настройки `.env` |
| [02-structure.md](02-structure.md) | Структура репозитория и поток данных от портала до ответа в Telegram |
| [03-csv-format.md](03-csv-format.md) | Источник CSV, формат полей, валидация снимка |
| [04-database.md](04-database.md) | Схема SQLite: три слоя, таблицы, индексы, представления, текущее наполнение |
| [05-import.md](05-import.md) | Синхронизация с порталом, zip-архив, diff-импорт с историей версий |
| [06-lookup.md](06-lookup.md) | Нормализация номера, поиск принадлежности, история номера |
| [07-bot.md](07-bot.md) | Telegram-бот: запуск, обработчики, журнал запросов |
| [08-testing.md](08-testing.md) | Тесты |
| [09-limitations.md](09-limitations.md) | Известные ограничения и что вне текущего этапа |

Быстрый старт:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env                              # вписать API_TOKEN
./getcsv.sh                                       # проверить портал / скачать / импортировать → archive/*.zip
.venv/bin/python -m checkph.importer --backfill   # при необходимости догрузить все снимки архива
.venv/bin/python main.py                          # запустить бота
```
