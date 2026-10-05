from __future__ import annotations

import sqlite3

import pytest

from checkph import enrichment


def test_phone_number_created_lazily(con):
    assert con.execute("SELECT count(*) AS n FROM phone_number").fetchone()["n"] == 0

    enrichment.record_spam_observation(con, 79001234567, "kaspersky", True, category="fraud", score=0.9)
    enrichment.record_spam_observation(con, 79001234567, "kaspersky", False)

    assert con.execute("SELECT count(*) AS n FROM phone_number").fetchone()["n"] == 1
    assert con.execute("SELECT count(*) AS n FROM number_spam_observation").fetchone()["n"] == 2
    source = con.execute("SELECT name, kind FROM data_source").fetchone()
    assert (source["name"], source["kind"]) == ("kaspersky", "spam_db")


def test_owner_current_view_returns_latest(con):
    number = 79001234567
    enrichment.record_owner_observation(con, number, "tbank", "Старый", "company", observed_at="2024-01-01 00:00:00", source_kind="bank")
    enrichment.record_owner_observation(con, number, "tbank", "Новый", "company", observed_at="2024-06-01 00:00:00", raw_payload={"k": 1})

    row = con.execute("SELECT owner_name, source_name FROM v_number_owner_current WHERE number = ?", (number,)).fetchone()
    assert (row["owner_name"], row["source_name"]) == ("Новый", "tbank")
    payload = con.execute("SELECT raw_payload FROM number_owner_observation WHERE owner_name = 'Новый'").fetchone()
    assert payload["raw_payload"] == '{"k": 1}'


def test_spam_view_ignores_stale_observations(con):
    number = 79001234567
    enrichment.record_spam_observation(con, number, "src", True, observed_at="2020-01-01 00:00:00")
    assert con.execute("SELECT * FROM v_number_spam_current WHERE number = ?", (number,)).fetchone() is None

    enrichment.record_spam_observation(con, number, "src", True)
    row = con.execute("SELECT is_spam, observations FROM v_number_spam_current WHERE number = ?", (number,)).fetchone()
    assert (row["is_spam"], row["observations"]) == (1, 1)


def test_invalid_number_rejected_by_schema(con):
    with pytest.raises(sqlite3.IntegrityError):
        enrichment.record_spam_observation(con, 9001234567, "src", True)
    assert con.execute("SELECT count(*) AS n FROM phone_number").fetchone()["n"] == 0
