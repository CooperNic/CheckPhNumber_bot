# Стек и окружение

[← Оглавление](project-docs.md)

## Стек

| Компонент | Версия / инструмент |
|---|---|
| Язык | Python 3.12 (интерпретатор окружения: 3.12.13) |
| Окружение | `.venv` в корне, создаётся как `python3.12 -m venv .venv` |
| Telegram | aiogram 3.31, long polling |
| Конфигурация | python-dotenv, файл `.env` |
| Хранилище | SQLite через стандартный `sqlite3`, WAL |
| Загрузка исходных данных | `checkph.sync` (`urllib`), обёртка `getcsv.sh`; архив — zip |
| Планировщик sync | systemd timer (`deploy/checkph-sync.timer`) или cron |
| Тесты | pytest (`requirements-dev.txt`) |
| Линтер, Docker, CI | нет |

Системный `python3` на машине — 3.10.12. Для проекта нужен интерпретатор `.venv` или явный `python3.12`.

## Установка

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # включает requirements.txt
```

`requirements.txt` — зависимости бота (`aiogram`, `python-dotenv`); `requirements-dev.txt` добавляет `pytest`.

## Настройки

Файл `.env` в корне (шаблон — `.env.example`):

| Переменная | Обязательна | По умолчанию | Смысл |
|---|---|---|---|
| `API_TOKEN` | да | — | токен Telegram-бота |
| `DB_PATH` | нет | `db.sqlite/registry.db` | путь к базе SQLite |
| `ACCESS_LOG` | нет | `access.log` | файл журнала запросов |

Все Python-скрипты считают пути относительными к текущему каталогу — запускать из корня репозитория.

В `.gitignore`: `.venv/`, `db.sqlite/`, `inCSV/`, `access.log`.
