"""Чтение и валидация снимка реестра (каталог или zip с CSV Минцифры).

Формат файла: UTF-8 с BOM, разделитель ``;``, первая строка — заголовок.
Колонки: АВС/DEF; От; До; Емкость; Оператор; Регион; Территория ГАР; ИНН.

Архивный снимок — zip (``ZIP_DEFLATED``) с CSV в корне архива. Хеш снимка
считается по байтам CSV (имя + содержимое), а не по контейнеру zip.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

CSV_FIELD_COUNT = 8
CSV_GLOB = "*.csv"
ZIP_COMPRESSLEVEL = 9


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


def is_zip_snapshot(path: str | Path) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    name = p.name.lower()
    return name.endswith(".zip") or name.endswith(".zip.partial") or zipfile.is_zipfile(p)


def snapshot_files(snapshot_dir: str | Path) -> list[Path]:
    """Список CSV в каталоге снимка (не для zip)."""
    files = sorted(Path(snapshot_dir).glob(CSV_GLOB))
    if not files:
        raise SnapshotError(f"В каталоге {snapshot_dir} нет файлов {CSV_GLOB}")
    return files


def _zip_csv_members(zf: zipfile.ZipFile) -> list[str]:
    names = sorted(
        n for n in zf.namelist()
        if n.lower().endswith(".csv") and "/" not in n.rstrip("/") and not n.endswith("/")
    )
    if not names:
        raise SnapshotError("В zip нет CSV в корне архива")
    return names


def _sha256_blobs(blobs: Iterator[tuple[str, bytes]] | list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for name, data in blobs:
        h.update(name.encode("utf-8"))
        h.update(data)
    return h.hexdigest()


def _iter_csv_blobs(snapshot: str | Path) -> Iterator[tuple[str, bytes]]:
    """Пары (имя файла, содержимое) в порядке имён — из каталога или zip."""
    path = Path(snapshot)
    if is_zip_snapshot(path):
        with zipfile.ZipFile(path, "r") as zf:
            for name in _zip_csv_members(zf):
                yield name, zf.read(name)
        return
    for f in snapshot_files(path):
        yield f.name, f.read_bytes()


def snapshot_sha256(snapshot: str | Path) -> str:
    """SHA-256 по байтам CSV снимка (имя + содержимое), одинаковый для каталога и zip."""
    return _sha256_blobs(_iter_csv_blobs(snapshot))


def pack_snapshot_zip(source_dir: str | Path, zip_path: str | Path) -> str:
    """Упаковывает CSV каталога в zip; возвращает ``snapshot_sha256`` содержимого."""
    source = Path(source_dir)
    dest = Path(zip_path)
    files = snapshot_files(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_COMPRESSLEVEL) as zf:
            for f in files:
                zf.write(f, arcname=f.name)
        digest = snapshot_sha256(tmp)
        if digest != snapshot_sha256(source):
            raise SnapshotError(f"Хеш zip {dest.name} не совпал с каталогом {source}")
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return digest


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
        yield from _iter_rows_from_text(fh, path.name)


def _iter_rows_from_text(fh, label: str) -> Iterator[RangeRow]:
    reader = csv.reader(fh, delimiter=";")
    next(reader, None)  # заголовок
    for line_no, fields in enumerate(reader, start=2):
        if not fields or all(not x.strip() for x in fields):
            continue
        yield _parse_line(fields, f"{label}:{line_no}")


def _iter_rows_from_bytes(name: str, data: bytes) -> Iterator[RangeRow]:
    text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", newline="")
    try:
        yield from _iter_rows_from_text(text, name)
    finally:
        text.detach()


def check_no_overlaps(rows: list[RangeRow]) -> None:
    """Диапазоны одного кода не должны пересекаться — от этого зависит поиск."""
    ordered = sorted(rows, key=lambda r: (r.code, r.from_number, r.to_number))
    for prev, cur in zip(ordered, ordered[1:]):
        if prev.code == cur.code and cur.from_number <= prev.to_number:
            raise SnapshotError(
                f"Пересечение диапазонов в коде {cur.code}: "
                f"{prev.from_number:07d}-{prev.to_number:07d} и {cur.from_number:07d}-{cur.to_number:07d}"
            )


def read_snapshot(snapshot: str | Path) -> list[RangeRow]:
    """Читает все CSV каталога или zip, валидирует и возвращает строки-диапазоны."""
    rows: list[RangeRow] = []
    for name, data in _iter_csv_blobs(snapshot):
        before = len(rows)
        rows.extend(_iter_rows_from_bytes(name, data))
        log.info("%s: %d диапазонов", name, len(rows) - before)
    check_no_overlaps(rows)
    return rows


def remove_dir(path: Path) -> None:
    """Удаляет каталог снимка после успешной упаковки в zip."""
    shutil.rmtree(path)
