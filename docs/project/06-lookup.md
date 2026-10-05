# Поиск принадлежности номера

[← Оглавление](project-docs.md)

Модуль `checkph/lookup.py`.

## Нормализация ввода

`normalize(text) -> int | None` убирает пробелы, дефисы, скобки и плюс. Принимаются 11 цифр с ведущей `7`; ведущая `8` заменяется на `7`. Всё остальное — `None`.

| Ввод | Результат |
|---|---|
| `79001234567` | `79001234567` |
| `+7 (900) 123-45-67` | `79001234567` |
| `8 900 123 45 67` | `79001234567` |
| `9001234567` | `None` |
| `abc` | `None` |

`split_number(number) -> (code, tail)`: `74951234567` → `(495, 1234567)`.

## Текущая принадлежность

`resolve(con, number) -> Attribution | None`.

Запрос берёт ближайший снизу открытый диапазон кода и проверяет верхнюю границу снаружи:

```sql
SELECT ... FROM (
    SELECT * FROM number_range
    WHERE code = :code AND from_number <= :tail AND valid_to_batch IS NULL
    ORDER BY from_number DESC LIMIT 1
) AS r
JOIN operator o ON o.id = r.operator_id
JOIN import_batch b ON b.id = r.valid_from_batch
WHERE r.to_number >= :tail
```

Корректность опирается на то, что открытые диапазоны одного кода не пересекаются. Проверка `to_number` во внешнем `WHERE` намеренно: внутри подзапроса она заставила бы при промахе сканировать индекс назад до начала кода (1 мс вместо микросекунд).

Обогащение читается двумя отдельными запросами к `v_number_owner_current` и `v_number_spam_current`. `LEFT JOIN` этих представлений в одном запросе с поиском обходится в ~110 мкс из-за материализации; раздельно — около 23 мкс на весь `resolve`.

Поля `Attribution`: `number`, `code`, `from_number`, `to_number`, `operator_name`, `inn`, `region`, `gar`, `since` (дата выгрузки, с которой действует версия), `owner_name`, `owner_kind`, `owner_source`, `is_spam` (`None`, если наблюдений нет).

`Attribution.display_region` — регион для показа: `region`, а если он пустой или `-` (так в выгрузках с 2026‑10‑04 у части строк) — `gar`. Функция `region_for_display(region, gar)` доступна отдельно.

## История номера

`history(con, number) -> list[HistoryEntry]` — все версии диапазонов, накрывавших номер, в порядке дат:

```sql
SELECT bf.source_date AS since, bt.source_date AS until, ...
FROM number_range r
JOIN operator o ON o.id = r.operator_id
JOIN import_batch bf ON bf.id = r.valid_from_batch
LEFT JOIN import_batch bt ON bt.id = r.valid_to_batch
WHERE r.code = ? AND r.from_number <= ? AND r.to_number >= ?
ORDER BY bf.source_date, r.id
```

`until = None` — версия действует. Разрыв между `until` одной записи и `since` следующей означает, что в промежуточных снимках номер ни в один диапазон не входил. Это и есть история номера при обновлениях реестра: в CSV нет информации о номере, которой нет о его диапазоне, поэтому хранить её поштучно не нужно.

Пример:

```python
from checkph import db, lookup
con = db.connect("db.sqlite/registry.db", read_only=True)
for h in lookup.history(con, 74965453205):
    print(h.since, "->", h.until, h.operator_name, h.inn)
```
