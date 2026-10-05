from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from checkph import db

HEADER = "\ufeffАВС/ DEF;От;До;Емкость;Оператор;Регион;Территория ГАР;ИНН\n"


def write_snapshot(root: Path, name: str, rows: list[str]) -> Path:
    """Создаёт каталог снимка с одним CSV; строки — без заголовка."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "ABC-4xx.csv").write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return d


@pytest.fixture
def con(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "registry.db")
    db.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def snapshots(tmp_path: Path) -> Path:
    return tmp_path / "archive"
