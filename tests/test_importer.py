from __future__ import annotations

import pytest

from checkph import importer
from checkph.csv_reader import SnapshotError, read_snapshot
from conftest import write_snapshot

R1 = '495;1000000;1009999;10000;ПАО "МГТС";г. Москва;Город Москва;7710016640'
R2 = '495;2000000;2000999;1000;ООО "Связь";г. Москва;Город Москва;7700000001'
R3 = '812;3000000;3000099;100;ПАО "Ростелеком";г. Санкт-Петербург;Город Санкт-Петербург;7707049388'


def open_ranges(con):
    return con.execute(
        "SELECT code, from_number, to_number FROM number_range WHERE valid_to_batch IS NULL ORDER BY code, from_number"
    ).fetchall()


def test_initial_import(con, snapshots):
    d = write_snapshot(snapshots, "20240101", [R1, R2, R3])
    res = importer.import_snapshot(con, d, "2024-01-01")

    assert not res.skipped
    assert (res.opened, res.closed, res.unchanged, res.operators_new) == (3, 0, 0, 3)
    assert [tuple(r) for r in open_ranges(con)] == [(495, 1000000, 1009999), (495, 2000000, 2000999), (812, 3000000, 3000099)]
    batch = con.execute("SELECT source_date, range_count FROM import_batch").fetchone()
    assert (batch["source_date"], batch["range_count"]) == ("2024-01-01", 3)


def test_reimport_same_date_is_noop(con, snapshots):
    d = write_snapshot(snapshots, "20240101", [R1, R2])
    importer.import_snapshot(con, d, "2024-01-01")
    res = importer.import_snapshot(con, d, "2024-01-01")

    assert res.skipped
    assert con.execute("SELECT count(*) AS n FROM number_range").fetchone()["n"] == 2
    assert con.execute("SELECT count(*) AS n FROM import_batch").fetchone()["n"] == 1


def test_identical_snapshot_creates_no_versions(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1, R2]), "2024-01-01")
    res = importer.import_snapshot(con, write_snapshot(snapshots, "b", [R1, R2]), "2024-02-01")

    assert (res.opened, res.closed, res.unchanged) == (0, 0, 2)
    assert con.execute("SELECT count(*) AS n FROM number_range").fetchone()["n"] == 2


def test_operator_rename_without_inn_change_creates_no_version(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-01-01")
    renamed = R1.replace('ПАО "МГТС"', 'ПАО МГТС')
    res = importer.import_snapshot(con, write_snapshot(snapshots, "b", [renamed]), "2024-02-01")

    assert (res.opened, res.closed, res.unchanged, res.operators_renamed) == (0, 0, 1, 1)
    op = con.execute("SELECT name FROM operator WHERE inn = '7710016640'").fetchone()
    assert op["name"] == "ПАО МГТС"
    variants = con.execute(
        "SELECT name, first_seen_batch, last_seen_batch FROM operator_name_variant ORDER BY first_seen_batch"
    ).fetchall()
    assert [tuple(v) for v in variants] == [('ПАО "МГТС"', 1, 1), ("ПАО МГТС", 2, 2)]


def test_inn_change_closes_and_opens_version(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-01-01")
    changed = R1.replace("7710016640", "7700000009").replace('ПАО "МГТС"', 'АО "Новый"')
    res = importer.import_snapshot(con, write_snapshot(snapshots, "b", [changed]), "2024-02-01")

    assert (res.opened, res.closed, res.unchanged) == (1, 1, 0)
    rows = con.execute(
        "SELECT valid_from_batch, valid_to_batch FROM number_range ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(1, 2), (2, None)]


def test_region_change_creates_version(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-01-01")
    changed = R1.replace("г. Москва;Город Москва", "Московская обл.;Московская область")
    res = importer.import_snapshot(con, write_snapshot(snapshots, "b", [changed]), "2024-02-01")

    assert (res.opened, res.closed) == (1, 1)
    assert len(open_ranges(con)) == 1


def test_removed_range_is_closed(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1, R2]), "2024-01-01")
    res = importer.import_snapshot(con, write_snapshot(snapshots, "b", [R1]), "2024-02-01")

    assert (res.opened, res.closed, res.unchanged) == (0, 1, 1)
    assert [tuple(r) for r in open_ranges(con)] == [(495, 1000000, 1009999)]
    closed = con.execute("SELECT valid_to_batch FROM number_range WHERE from_number = 2000000").fetchone()
    assert closed["valid_to_batch"] == 2


def test_split_range_closes_old_and_opens_two(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-01-01")
    half1 = '495;1000000;1004999;5000;ПАО "МГТС";г. Москва;Город Москва;7710016640'
    half2 = '495;1005000;1009999;5000;ООО "Связь";г. Москва;Город Москва;7700000001'
    res = importer.import_snapshot(con, write_snapshot(snapshots, "b", [half1, half2]), "2024-02-01")

    assert (res.opened, res.closed) == (2, 1)
    assert len(open_ranges(con)) == 2


def test_overlapping_ranges_rejected(con, snapshots):
    bad = '495;1005000;1010000;5001;ООО "Связь";г. Москва;Город Москва;7700000001'
    d = write_snapshot(snapshots, "a", [R1, bad])
    with pytest.raises(SnapshotError, match="Пересечение"):
        read_snapshot(d)
    with pytest.raises(SnapshotError):
        importer.import_snapshot(con, d, "2024-01-01")
    assert con.execute("SELECT count(*) AS n FROM import_batch").fetchone()["n"] == 0


def test_out_of_order_snapshot_rejected(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-02-01")
    with pytest.raises(ValueError, match="порядок"):
        importer.import_snapshot(con, write_snapshot(snapshots, "b", [R1]), "2024-01-01")


def test_empty_inn_operator_keyed_by_name(con, snapshots):
    no_inn = '495;4000000;4000009;10;ООО "Без ИНН";г. Москва;Город Москва;'
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [no_inn]), "2024-01-01")
    importer.import_snapshot(con, write_snapshot(snapshots, "b", [no_inn]), "2024-02-01")

    ops = con.execute("SELECT inn, name FROM operator").fetchall()
    assert [tuple(o) for o in ops] == [(None, 'ООО "Без ИНН"')]
    assert con.execute("SELECT count(*) AS n FROM number_range").fetchone()["n"] == 1


def test_most_frequent_spelling_becomes_canonical(con, snapshots):
    a = '495;1000000;1000009;10;ООО "Т2 МОБАЙЛ";Москва;Москва;7743895280'
    b = '495;1000010;1000019;10;ООО "Т2 МОБАЙЛ";Москва;Москва;7743895280'
    c = '495;1000020;1000029;10;ООО "Т2 Мобайл";Москва;Москва;7743895280'
    res = importer.import_snapshot(con, write_snapshot(snapshots, "a", [a, b, c]), "2024-01-01")

    assert res.operators_new == 1
    assert con.execute("SELECT name FROM operator").fetchone()["name"] == 'ООО "Т2 МОБАЙЛ"'
    assert con.execute("SELECT count(*) AS n FROM operator_name_variant").fetchone()["n"] == 2


def test_backfill_orders_by_dir_name_and_is_idempotent(con, snapshots):
    write_snapshot(snapshots, "20240201", [R1, R2])
    write_snapshot(snapshots, "20240101", [R1])
    (snapshots / "notes").mkdir()

    first = importer.backfill(con, snapshots)
    assert [r.source_date for r in first] == ["2024-01-01", "2024-02-01"]
    assert [r.opened for r in first] == [1, 1]

    second = importer.backfill(con, snapshots)
    assert all(r.skipped for r in second)
    assert con.execute("SELECT count(*) AS n FROM number_range").fetchone()["n"] == 2


def test_date_from_dir_name():
    assert importer.date_from_dir_name("20261004") == "2026-10-04"
    assert importer.date_from_dir_name("20261005-0010") == "2026-10-05T00:10"
    with pytest.raises(ValueError):
        importer.date_from_dir_name("latest")
