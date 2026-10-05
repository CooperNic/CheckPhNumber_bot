"""Подключение к SQLite и инициализация схемы."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = "db.sqlite/registry.db"


def connect(path: str | Path = DEFAULT_DB_PATH, *, read_only: bool = False) -> sqlite3.Connection:
    """Открывает соединение в режиме autocommit.

    Транзакции управляются явно через :func:`transaction`. Соединение можно
    использовать из других потоков (``check_same_thread=False``), но вызовы
    должны быть сериализованы вызывающей стороной.
    """
    path = Path(path)
    if read_only:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, check_same_thread=False)
    con.isolation_level = None  # autocommit; BEGIN/COMMIT вручную
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    if not read_only:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    """Создаёт таблицы, индексы и представления (идемпотентно)."""
    sql = resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")
    con.executescript(sql)


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Явная транзакция: COMMIT при успехе, ROLLBACK при исключении."""
    con.execute("BEGIN")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")
