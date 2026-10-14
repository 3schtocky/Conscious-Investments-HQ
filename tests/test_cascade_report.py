"""Cascade reporting (phase C0): tier spend and escalation rate, read from the ledger and task rows."""

from __future__ import annotations

import pytest

from hq import cascade
from hq.store import Store

SONNET, HAIKU = "claude-sonnet-5-5", "claude-haiku-4-5"
USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "office.db")

    def spend(agent, task, model, cost):
        s.add_spend(day="2026-10-14", agent=agent, task_id=task, root_id=task, model=model,
                    usage=USAGE, cost=cost, request_id=None)

    ok = s.create_task(assignee="quant_associate", assigned_by="captain", kind="assignment", title="Model A", body="x")
    s.set_task_status(ok, "done")
    spend("quant_associate", ok, HAIKU, 0.10)
    hard = s.create_task(assignee="quant_associate", assigned_by="captain", kind="assignment", title="Model B", body="x")
    s.set_task_status(hard, "done")
    spend("quant_associate", hard, HAIKU, 0.20)
    s.create_task(assignee="quant_lead", assigned_by="office", kind="polish", title="Polish B", body="x",
                  root_id=hard, attempt=2, escalated_from=hard, escalation_reason="gate_fails")
    spend("quant_lead", hard, SONNET, 0.30)
    return s


def test_spend_splits_by_tier(store):
    r = cascade.by_tier(store)
    assert r["tiers"]["associate"]["cost"] == pytest.approx(0.30)
    assert r["tiers"]["lead"]["cost"] == pytest.approx(0.30)


def test_escalation_rate_and_reasons(store):
    c = cascade.summary(store)
    assert c["haiku_first"] == 2 and c["escalated"] == 1 and c["escalation_rate"] == 0.5
    assert c["finished_on_haiku"] == 1 and c["reasons"] == {"gate_fails": 1}


def test_old_database_gains_the_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, created REAL, updated REAL, assignee TEXT, assigned_by TEXT,"
               " kind TEXT, parent_id INTEGER, root_id INTEGER, title TEXT, body TEXT, status TEXT, status_reason TEXT, result TEXT)")
    db.commit(); db.close()
    t = Store(path).create_task(assignee="a", assigned_by="b", kind="assignment", title="t", body="x")
    assert Store(path).task(t)["attempt"] == 1


def test_summary_can_be_limited_to_a_day(store):
    assert cascade.summary(store, "1999-01-01")["haiku_first"] == 0
