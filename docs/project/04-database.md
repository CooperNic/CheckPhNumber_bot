# База данных

[← Оглавление](project-docs.md)

Файл: `db.sqlite/registry.db` (путь — `DB_PATH` в `.env`). Схема — `checkph/schema.sql`, применяется `db.init_schema()` идемпотентно (`IF NOT EXISTS`). Соединение открывается `db.connect()` в режиме autocommit (WAL, `synchronous=NORMAL`, `foreign_keys=ON`); транзакции — явно через контекст `db.transaction(con)`.

## Три слоя

```
┌────────────────────────────────────────────────────────────┐
│ Слой 1. Реестр (источник: CSV Минцифры)                     │
│   import_batch → number_range (SCD Type 2) → operator       │
│   истина о принадлежности, версионируется по диапазонам     │
├────────────────────────────────────────────────────────────┤
│ Слой 2. Номер как сущность                                  │
│   phone_number — строка появляется по факту, лениво         │
├────────────────────────────────────────────────────────────┤
│ Слой 3. Обогащение (источники: банки, спам-базы, юзеры)     │
│   *_observation — append-only, история встроена             │
└────────────────────────────────────────────────────────────┘
```

Слои связаны только вычислением: номер `7CCCTTTTTTT` → `(code, tail)` → диапазон. Внешнего ключа от номера к диапазону нет — диапазон меняется независимо от номера. Реестр хранится диапазонами, а не номерами: в CSV нет информации о номере, которой нет о его диапазоне, а полная материализация 796 млн номеров давала файл в 134 ГБ.

## Слой 1. Реестр

| Таблица | Назначение |
|---|---|
| `import_batch` | снимок: `source_date` (уникальна, `YYYY-MM-DD`), `imported_at`, `files_sha256`, `range_count` |
| `operator` | оператор; ключ — `inn` (уникален, может быть NULL — тогда уникальность по `name`); `name` — самое частое написание в последнем снимке |
| `operator_name_variant` | все встреченные написания оператора с `first_seen_batch`/`last_seen_batch` |
| `number_range` | версия диапазона: `code`, `from_number`, `to_number`, `operator_id`, `region_raw`, `gar_raw`, `valid_from_batch`, `valid_to_batch` (NULL = действует) |

Ограничения: `code` 100–999, границы 0–9 999 999, `to_number >= from_number`, ИНН 10 или 12 цифр.

Индексы `number_range`:

| Индекс | Определение | Назначение |
|---|---|---|
| `ix_range_current` | частичный уникальный `(code, from_number) WHERE valid_to_batch IS NULL` | поиск текущей принадлежности |
| `ix_range_all` | `(code, from_number, to_number)` | история номера |
| `ix_range_valid_to` | `(valid_to_batch)` | закрытие версий при импорте |

Уникальность `(code, from_number)` среди открытых версий держится на свойстве снимка «диапазоны одного кода не пересекаются», которое проверяет `csv_reader`.

## Слой 2. Номер

`phone_number(number INTEGER PK, created_at)` — `number` в виде `7XXXXXXXXXX`, CHECK на диапазон 71000000000–79999999999. Строка создаётся лениво при первом наблюдении (`INSERT OR IGNORE` в `enrichment.py`). Все таблицы слоя 3 ссылаются на неё внешним ключом.

## Слой 3. Обогащение

| Таблица | Назначение |
|---|---|
| `data_source` | источник: `name` (уникально), `kind` ∈ `bank`, `spam_db`, `user_report`, `other`, `url` |
| `number_owner_observation` | наблюдение владельца: `owner_name`, `owner_kind` ∈ `person`, `company`, `unknown`, `observed_at`, `raw_payload` (JSON как есть) |
| `number_spam_observation` | спам-наблюдение: `is_spam`, `category`, `score`, `observed_at` |
| `lookup_event` | журнал запросов к боту: `number` (NULL, если ввод не распознан), `raw_input`, `tg_user_id`, `requested_at`, `found`, `duration_ms` |

Наблюдения только добавляются — история есть по построению. Текущее состояние отдают представления:

- `v_number_owner_current` — последнее наблюдение владельца по номеру (с именем источника);
- `v_number_spam_current` — по номеру `max(is_spam)`, число наблюдений и дата последнего за 180 дней.

API записи — `checkph/enrichment.py`:

```python
record_owner_observation(con, number, source_name, owner_name, owner_kind="unknown",
                         observed_at=None, raw_payload=None, source_kind="other") -> id
record_spam_observation(con, number, source_name, is_spam, category=None, score=None,
                        observed_at=None, source_kind="spam_db") -> id
```

Обе функции в одной транзакции создают `phone_number` и `data_source` при отсутствии и вставляют наблюдение. Новый тип данных о номере — новая таблица `number_<что>_observation` по тому же шаблону.

## Текущее наполнение

После `--backfill` по пяти снимкам (2024‑07‑24, 2024‑08‑09, 2024‑09‑13, 2024‑11‑11, 2026‑10‑04): 551 158 версий диапазонов, из них 451 099 открытых; 1 416 операторов, 2 298 вариантов написаний; файл около 85 МБ. Импорт всех пяти снимков — около 30 с.

Посмотреть схему и состояние:

```bash
sqlite3 "file:db.sqlite/registry.db?mode=ro" ".schema number_range"
sqlite3 "file:db.sqlite/registry.db?mode=ro" "SELECT source_date, range_count FROM import_batch ORDER BY id"
```
