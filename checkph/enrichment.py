"""Запись наблюдений о номере из внешних источников (слой 3).

Строка в ``phone_number`` создаётся лениво — при первом наблюдении.
Наблюдения append-only: история хранится по построению, текущее состояние
отдают представления ``v_number_owner_current`` и ``v_number_spam_current``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from . import db

SourceKind = Literal["bank", "spam_db", "user_report", "other"]
OwnerKind = Literal["person", "company", "unknown"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ensure_source(con: sqlite3.Connection, name: str, kind: SourceKind = "other", url: str | None = None) -> int:
    row = con.execute("SELECT id FROM data_source WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row["id"]
    cur = con.execute("INSERT INTO data_source (name, kind, url) VALUES (?, ?, ?)", (name, kind, url))
    return cur.lastrowid


def ensure_phone_number(con: sqlite3.Connection, number: int) -> None:
    con.execute("INSERT OR IGNORE INTO phone_number (number) VALUES (?)", (number,))


def record_owner_observation(
    con: sqlite3.Connection,
    number: int,
    source_name: str,
    owner_name: str | None,
    owner_kind: OwnerKind = "unknown",
    observed_at: str | None = None,
    raw_payload: Any = None,
    source_kind: SourceKind = "other",
) -> int:
    """Фиксирует наблюдение владельца; возвращает id наблюдения."""
    payload = None if raw_payload is None else json.dumps(raw_payload, ensure_ascii=False)
    with db.transaction(con):
        ensure_phone_number(con, number)
        source_id = ensure_source(con, source_name, source_kind)
        cur = con.execute(
            "INSERT INTO number_owner_observation (number, source_id, owner_name, owner_kind, observed_at, raw_payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (number, source_id, owner_name, owner_kind, observed_at or _now(), payload),
        )
        return cur.lastrowid


def record_spam_observation(
    con: sqlite3.Connection,
    number: int,
    source_name: str,
    is_spam: bool,
    category: str | None = None,
    score: float | None = None,
    observed_at: str | None = None,
    source_kind: SourceKind = "spam_db",
) -> int:
    """Фиксирует спам-наблюдение; возвращает id наблюдения."""
    with db.transaction(con):
        ensure_phone_number(con, number)
        source_id = ensure_source(con, source_name, source_kind)
        cur = con.execute(
            "INSERT INTO number_spam_observation (number, source_id, is_spam, category, score, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (number, source_id, int(is_spam), category, score, observed_at or _now()),
        )
        return cur.lastrowid
