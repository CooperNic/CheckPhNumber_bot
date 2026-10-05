# Telegram-бот

[← Оглавление](project-docs.md)

Модуль `checkph/bot.py`, точка входа `main.py`. Публичный бот: https://t.me/ProverkaNomeraTelefonaBot

## Запуск

```bash
.venv/bin/python main.py
```

`bot.main()`:

1. читает `.env` (`API_TOKEN` обязателен, иначе выход с ошибкой; `DB_PATH`, `ACCESS_LOG`, `TELEGRAM_PROXY` — необязательны);
2. открывает `access.log` на дозапись;
3. открывает базу и вызывает `init_schema()` (идемпотентно);
4. создаёт `Repository` и запускает long polling через `TrustEnvAiohttpSession` (учитывает `https_proxy` / `TELEGRAM_PROXY`, как curl); соединение закрывается при остановке.

Если с сервера `curl` до Telegram ходит только через прокси, а бот без прокси получает `Request timeout error` — задайте в окружении `https_proxy`/`http_proxy` или `TELEGRAM_PROXY` в `.env`.

## Доступ к базе

`Repository` — обёртка над одним соединением SQLite с `threading.Lock`. Обработчики вызывают её методы через `asyncio.to_thread`, чтобы чтение с диска не блокировало event loop; блокировка сериализует обращения из рабочих потоков.

- `resolve(number)` → `lookup.resolve`;
- `log_lookup(number, raw_input, tg_user_id, found, duration_ms)` → строка в `lookup_event`.

## Обработчики

| Сообщение | Поведение |
|---|---|
| `/start` | текст с форматом ввода `7<код><номер>` |
| любое другое | `normalize` → при ошибке формата подсказка «нужны 11 цифр, начиная с 7»; иначе `resolve` → ответ или `Номер не найден.` |

Ответ при найденном номере (`format_answer`):

```
<оператор> (<регион>)
Владелец: <owner_name> (источник: <source>)     # если есть наблюдение
Отмечен как спам.                               # если is_spam за 180 дней
```

Регион — `Attribution.display_region` (при `-` в выгрузке показывается территория ГАР).

## Журналирование

Каждый запрос пишется в два места:

- таблица `lookup_event`: номер (NULL, если ввод не распознан), исходный текст, id пользователя Telegram, найден ли, длительность в мс;
- файл `access.log` (логгер `checkph.access`), формат строки: `исходный ввод|номер|оператор|регион|имя|@username|id|секунды`; при промахе — `исходный ввод|Номер не найден.|||…`.

Оба содержат номера телефонов и идентификаторы пользователей; `access.log` в `.gitignore`.
