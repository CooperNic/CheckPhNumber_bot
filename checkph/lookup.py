"""Поиск принадлежности номера по реестру и его история."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_NON_DIGITS = re.compile(r"[\s\-\(\)\+]")


@dataclass(frozen=True, slots=True)
class Attribution:
    number: int
    code: int
    from_number: int
    to_number: int
    operator_name: str
    inn: str | None
    region: str
    gar: str
    since: str                       # дата выгрузки, с которой действует версия
    owner_name: str | None = None
    owner_kind: str | None = None
    owner_source: str | None = None
    is_spam: bool | None = None      # None = наблюдений нет

    @property
    def display_region(self) -> str:
        """Регион для показа: в выгрузках с 2026 года колонка «Регион» бывает ``-``."""
        return region_for_display(self.region, self.gar)


def region_for_display(region: str, gar: str) -> str:
    region = region.strip()
    return region if region and region != "-" else gar.strip()


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    since: str
    until: str | None                # None = действует
    from_number: int
    to_number: int
    operator_name: str
    inn: str | None
    region: str
    gar: str


def normalize(text: str) -> int | None:
    """Приводит ввод пользователя к числу 7XXXXXXXXXX или возвращает None.

    Удаляются пробелы, дефисы, скобки и плюс. Принимается 11 цифр, начинающихся
    с 7, либо 8 (заменяется на 7).
    """
    digits = _NON_DIGITS.sub("", text.strip())
    if not digits.isdigit() or len(digits) != 11:
        return None
    if digits[0] == "8":
        digits = "7" + digits[1:]
    if digits[0] != "7":
        return None
    return int(digits)


def split_number(number: int) -> tuple[int, int]:
    """7CCCTTTTTTT -> (code, tail)."""
    return (number // 10_000_000) % 1000, number % 10_000_000


# Внутри снимка диапазоны одного кода не пересекаются, поэтому достаточно взять
# ближайший снизу открытый диапазон и проверить его верхнюю границу снаружи.
# Проверка to_number во внешнем WHERE намеренно: внутри подзапроса она заставила бы
# при промахе сканировать индекс назад до начала кода.
_RESOLVE_SQL = """
SELECT r.from_number, r.to_number, r.region_raw, r.gar_raw,
       o.name AS operator_name, o.inn,
       b.source_date AS since
FROM (
    SELECT * FROM number_range
    WHERE code = :code AND from_number <= :tail AND valid_to_batch IS NULL
    ORDER BY from_number DESC
    LIMIT 1
) AS r
JOIN operator     AS o ON o.id = r.operator_id
JOIN import_batch AS b ON b.id = r.valid_from_batch
WHERE r.to_number >= :tail
"""

# Обогащение читается отдельными запросами: LEFT JOIN представлений в одном
# запросе с поиском по реестру обходится на порядок дороже из-за материализации.
_OWNER_SQL = "SELECT owner_name, owner_kind, source_name FROM v_number_owner_current WHERE number = ?"
_SPAM_SQL = "SELECT is_spam FROM v_number_spam_current WHERE number = ?"


def resolve(con: sqlite3.Connection, number: int) -> Attribution | None:
    code, tail = split_number(number)
    row = con.execute(_RESOLVE_SQL, {"code": code, "tail": tail}).fetchone()
    if row is None:
        return None
    owner = con.execute(_OWNER_SQL, (number,)).fetchone()
    spam = con.execute(_SPAM_SQL, (number,)).fetchone()
    return Attribution(
        number=number,
        code=code,
        from_number=row["from_number"],
        to_number=row["to_number"],
        operator_name=row["operator_name"],
        inn=row["inn"],
        region=row["region_raw"],
        gar=row["gar_raw"],
        since=row["since"],
        owner_name=owner["owner_name"] if owner else None,
        owner_kind=owner["owner_kind"] if owner else None,
        owner_source=owner["source_name"] if owner else None,
        is_spam=bool(spam["is_spam"]) if spam else None,
    )


_HISTORY_SQL = """
SELECT bf.source_date AS since, bt.source_date AS until,
       r.from_number, r.to_number, r.region_raw, r.gar_raw,
       o.name AS operator_name, o.inn
FROM number_range AS r
JOIN operator          AS o  ON o.id  = r.operator_id
JOIN import_batch      AS bf ON bf.id = r.valid_from_batch
LEFT JOIN import_batch AS bt ON bt.id = r.valid_to_batch
WHERE r.code = ? AND r.from_number <= ? AND r.to_number >= ?
ORDER BY bf.source_date, r.id
"""


def history(con: sqlite3.Connection, number: int) -> list[HistoryEntry]:
    code, tail = split_number(number)
    return [
        HistoryEntry(
            since=r["since"],
            until=r["until"],
            from_number=r["from_number"],
            to_number=r["to_number"],
            operator_name=r["operator_name"],
            inn=r["inn"],
            region=r["region_raw"],
            gar=r["gar_raw"],
        )
        for r in con.execute(_HISTORY_SQL, (code, tail, tail))
    ]
