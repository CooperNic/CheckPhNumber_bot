"""Diff-импорт снимка реестра в версионируемую таблицу диапазонов (SCD Type 2).

Правила для диапазона с ключом ``(code, from_number, to_number)``:

* есть в базе, нет в снимке          -> закрыть версию (``valid_to_batch``);
* нет в базе                          -> открыть версию;
* есть, но другой оператор/регион/ГАР -> закрыть старую, открыть новую;
* совпадает                           -> ничего.

Операторы идентифицируются по ИНН, поэтому смена написания имени без смены
ИНН не порождает версий диапазона — она фиксируется в ``operator_name_variant``.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import db
from .csv_reader import RangeRow, read_snapshot, snapshot_sha256

log = logging.getLogger(__name__)

ARCHIVE_DIR = Path("inCSV/archive")
# YYYYMMDD или YYYYMMDD-HHMM (повторная публикация в тот же день).
_DATE_STEM_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:-(\d{2})(\d{2}))?$")


@dataclass(slots=True)
class ImportResult:
    source_date: str
    batch_id: int | None
    skipped: bool = False
    ranges_in_snapshot: int = 0
    opened: int = 0
    closed: int = 0
    unchanged: int = 0
    operators_new: int = 0
    operators_renamed: int = 0

    def summary(self) -> str:
        if self.skipped:
            return f"{self.source_date}: уже импортирован, пропуск"
        return (
            f"{self.source_date}: batch={self.batch_id} диапазонов={self.ranges_in_snapshot} "
            f"открыто={self.opened} закрыто={self.closed} без изменений={self.unchanged} "
            f"операторов новых={self.operators_new} переименовано={self.operators_renamed}"
        )


def date_from_dir_name(name: str) -> str:
    """Дата снимка из имени каталога или stem zip: YYYYMMDD → YYYY-MM-DD, YYYYMMDD-HHMM → YYYY-MM-DDTHH:MM."""
    stem = name[:-4] if name.lower().endswith(".zip") else name
    m = _DATE_STEM_RE.match(stem)
    if not m:
        raise ValueError(f"Имя снимка {name!r} не похоже на YYYYMMDD или YYYYMMDD-HHMM")
    date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if m.group(4) is not None:
        return f"{date}T{m.group(4)}:{m.group(5)}"
    return date


def list_archive_snapshots(archive_dir: str | Path) -> list[Path]:
    """Каталоги YYYYMMDD и файлы YYYYMMDD.zip / YYYYMMDD-HHMM.zip по возрастанию имени."""
    root = Path(archive_dir)
    items: list[Path] = []
    if not root.is_dir():
        return items
    for p in root.iterdir():
        if p.name.startswith(".") or p.name.startswith("_"):
            continue
        if p.is_dir() and _DATE_STEM_RE.match(p.name):
            items.append(p)
        elif p.is_file() and p.suffix.lower() == ".zip" and _DATE_STEM_RE.match(p.stem):
            items.append(p)
    return sorted(items, key=lambda p: p.name)

def _operator_key(row: RangeRow) -> tuple[str | None, str]:
    """Ключ оператора: ИНН, а при его отсутствии — имя."""
    return (row.inn, "") if row.inn is not None else (None, row.operator_name)


def _resolve_operators(
    con: sqlite3.Connection, rows: list[RangeRow], batch_id: int, result: ImportResult
) -> dict[tuple[str | None, str], int]:
    """Создаёт/обновляет операторов и варианты написаний; возвращает key -> operator.id."""
    names_by_key: dict[tuple[str | None, str], Counter[str]] = defaultdict(Counter)
    for r in rows:
        names_by_key[_operator_key(r)][r.operator_name] += 1

    ids: dict[tuple[str | None, str], int] = {}
    for key, names in names_by_key.items():
        inn, name_key = key
        canonical = names.most_common(1)[0][0]
        if inn is not None:
            found = con.execute("SELECT id, name FROM operator WHERE inn = ?", (inn,)).fetchone()
        else:
            found = con.execute("SELECT id, name FROM operator WHERE inn IS NULL AND name = ?", (name_key,)).fetchone()

        if found is None:
            cur = con.execute("INSERT INTO operator (inn, name) VALUES (?, ?)", (inn, canonical))
            op_id = cur.lastrowid
            result.operators_new += 1
        else:
            op_id = found["id"]
            if found["name"] != canonical:
                con.execute("UPDATE operator SET name = ? WHERE id = ?", (canonical, op_id))
                result.operators_renamed += 1
        ids[key] = op_id

        con.executemany(
            """
            INSERT INTO operator_name_variant (operator_id, name, first_seen_batch, last_seen_batch)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (operator_id, name) DO UPDATE SET last_seen_batch = excluded.last_seen_batch
            """,
            [(op_id, n, batch_id, batch_id) for n in names],
        )
    return ids


def _load_current_ranges(con: sqlite3.Connection) -> dict[tuple[int, int, int], tuple[int, int, str, str]]:
    """key -> (id, operator_id, region_raw, gar_raw) для открытых версий."""
    cur = con.execute(
        "SELECT id, code, from_number, to_number, operator_id, region_raw, gar_raw "
        "FROM number_range WHERE valid_to_batch IS NULL"
    )
    return {
        (r["code"], r["from_number"], r["to_number"]): (r["id"], r["operator_id"], r["region_raw"], r["gar_raw"])
        for r in cur
    }


def import_snapshot(con: sqlite3.Connection, snapshot: str | Path, source_date: str) -> ImportResult:
    """Импортирует один снимок (каталог или zip). Повторный вызов с той же датой ничего не меняет."""
    result = ImportResult(source_date=source_date, batch_id=None)
    existing = con.execute("SELECT id FROM import_batch WHERE source_date = ?", (source_date,)).fetchone()
    if existing is not None:
        result.batch_id = existing["id"]
        result.skipped = True
        return result

    latest = con.execute("SELECT max(source_date) AS d FROM import_batch").fetchone()["d"]
    if latest is not None and source_date <= latest:
        raise ValueError(f"Снимок {source_date} не новее последнего импортированного ({latest}); порядок импорта нарушен")

    rows = read_snapshot(snapshot)
    result.ranges_in_snapshot = len(rows)
    digest = snapshot_sha256(snapshot)

    with db.transaction(con):
        cur = con.execute(
            "INSERT INTO import_batch (source_date, files_sha256, range_count) VALUES (?, ?, ?)",
            (source_date, digest, len(rows)),
        )
        batch_id = cur.lastrowid
        result.batch_id = batch_id

        op_ids = _resolve_operators(con, rows, batch_id, result)
        current = _load_current_ranges(con)

        to_close: list[int] = []
        to_open: list[tuple[int, int, int, int, str, str, int]] = []
        seen: set[tuple[int, int, int]] = set()

        for r in rows:
            key = r.key
            seen.add(key)
            value = (op_ids[_operator_key(r)], r.region_raw, r.gar_raw)
            cur_row = current.get(key)
            if cur_row is None:
                to_open.append((*key, *value, batch_id))
            elif cur_row[1:] != value:
                to_close.append(cur_row[0])
                to_open.append((*key, *value, batch_id))
            else:
                result.unchanged += 1

        for key, cur_row in current.items():
            if key not in seen:
                to_close.append(cur_row[0])

        con.executemany(
            "UPDATE number_range SET valid_to_batch = ? WHERE id = ?",
            [(batch_id, rid) for rid in to_close],
        )
        con.executemany(
            "INSERT INTO number_range (code, from_number, to_number, operator_id, region_raw, gar_raw, valid_from_batch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            to_open,
        )
        result.closed = len(to_close)
        result.opened = len(to_open)

    return result


def backfill(con: sqlite3.Connection, archive_dir: str | Path = ARCHIVE_DIR) -> list[ImportResult]:
    """Импортирует все снимки ``archive/YYYYMMDD`` и ``*.zip`` по возрастанию имени."""
    snaps = list_archive_snapshots(archive_dir)
    if not snaps:
        raise ValueError(f"В {archive_dir} нет снимков вида YYYYMMDD или YYYYMMDD.zip")
    results = []
    for snap in snaps:
        res = import_snapshot(con, snap, date_from_dir_name(snap.name))
        log.info(res.summary())
        results.append(res)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт снимка реестра нумерации в SQLite")
    parser.add_argument("--db", default=db.DEFAULT_DB_PATH, help="путь к базе (по умолчанию %(default)s)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dir", help="каталог или zip одного снимка")
    group.add_argument(
        "--backfill",
        action="store_true",
        help=f"импортировать все снимки {ARCHIVE_DIR}/YYYYMMDD(.zip) по порядку",
    )
    parser.add_argument("--date", help="дата снимка (для --dir; по умолчанию из имени)")
    parser.add_argument("--archive", default=str(ARCHIVE_DIR), help="каталог архива для --backfill")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    con = db.connect(args.db)
    db.init_schema(con)
    try:
        if args.backfill:
            backfill(con, args.archive)
        else:
            source_date = args.date or date_from_dir_name(Path(args.dir).name)
            print(import_snapshot(con, args.dir, source_date).summary())
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
