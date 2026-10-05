"""Чтение и валидация снимка реестра (каталог с CSV Минцифры).

Формат файла: UTF-8 с BOM, разделитель ``;``, первая строка — заголовок.
Колонки: АВС/DEF; От; До; Емкость; Оператор; Регион; Территория ГАР; ИНН.
"""

from __future__ import annotations

import csv
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

log = logging.getLogger(__name__)

CSV_FIELD_COUNT = 8
CSV_GLOB = "*.csv"


class SnapshotError(ValueError):
    """Снимок непригоден для импорта."""


@dataclass(frozen=True, slots=True)
class RangeRow:
    code: int
    from_number: int
    to_number: int
    operator_name: str
    region_raw: str
    gar_raw: str
    inn: str | None

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.code, self.from_number, self.to_number)


def snapshot_files(snapshot_dir: str | Path) -> list[Path]:
    files = sorted(Path(snapshot_dir).glob(CSV_GLOB))
    if not files:
        raise SnapshotError(f"В каталоге {snapshot_dir} нет файлов {CSV_GLOB}")
    return files


def snapshot_sha256(files: Iterable[Path]) -> str:
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()


def _parse_line(fields: list[str], where: str) -> RangeRow:
    if len(fields) != CSV_FIELD_COUNT:
        raise SnapshotError(f"{where}: ожидалось {CSV_FIELD_COUNT} полей, получено {len(fields)}")
    code_s, from_s, to_s, amount_s, operator, region, gar, inn = (x.strip() for x in fields)

    if not (code_s.isdigit() and len(code_s) == 3):
        raise SnapshotError(f"{where}: код ABC/DEF должен быть тремя цифрами, получено {code_s!r}")
    if not (from_s.isdigit() and to_s.isdigit() and len(from_s) == 7 and len(to_s) == 7):
        raise SnapshotError(f"{where}: границы диапазона должны быть семизначными, получено {from_s!r}..{to_s!r}")
    code, from_number, to_number = int(code_s), int(from_s), int(to_s)
    if from_number > to_number:
        raise SnapshotError(f"{where}: начало диапазона больше конца ({from_number} > {to_number})")
    if not operator:
        raise SnapshotError(f"{where}: пустое имя оператора")

    if amount_s.isdigit() and int(amount_s) != to_number - from_number + 1:
        log.warning("%s: ёмкость %s не совпадает с размером диапазона %d", where, amount_s, to_number - from_number + 1)

    inn_value: str | None = inn or None
    if inn_value is not None and not (inn_value.isdigit() and len(inn_value) in (10, 12)):
        raise SnapshotError(f"{where}: некорректный ИНН {inn_value!r}")

    return RangeRow(code, from_number, to_number, operator, region, gar, inn_value)


def iter_rows(path: Path) -> Iterator[RangeRow]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader, None)  # заголовок
        for line_no, fields in enumerate(reader, start=2):
            if not fields or all(not x.strip() for x in fields):
                continue
            yield _parse_line(fields, f"{path.name}:{line_no}")


def check_no_overlaps(rows: list[RangeRow]) -> None:
    """Диапазоны одного кода не должны пересекаться — от этого зависит поиск."""
    ordered = sorted(rows, key=lambda r: (r.code, r.from_number, r.to_number))
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.code == cur.code and cur.from_number <= prev.to_number:
            raise SnapshotError(
                f"Пересечение диапазонов в коде {cur.code}: "
                f"{prev.from_number:07d}-{prev.to_number:07d} и {cur.from_number:07d}-{cur.to_number:07d}"
            )


def read_snapshot(snapshot_dir: str | Path) -> list[RangeRow]:
    """Читает все CSV каталога, валидирует и возвращает строки-диапазоны."""
    rows: list[RangeRow] = []
    for f in snapshot_files(snapshot_dir):
        before = len(rows)
        rows.extend(iter_rows(f))
        log.info("%s: %d диапазонов", f.name, len(rows) - before)
    check_no_overlaps(rows)
    return rows
