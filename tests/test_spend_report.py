"""`hq spend --by-task`: which tasks cost the money, and how big a conversation each call carried."""

from __future__ import annotations

import pytest

from hq.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "office.db")
    big = s.create_task(assignee="er_lead", assigned_by="captain", kind="task", title="Big memo", body="x")
    small = s.create_task(assignee="quant_lead", assigned_by="captain", kind="task", title="Re-run model", body="x")
    for task, agent, day, ctx, out, cost in [(big, "er_lead", "2026-10-03", 120_000, 2_000, 0.30),
                                             (big, "er_lead", "2026-10-03", 124_000, 3_000, 0.40),
                                             (small, "quant_lead", "2026-10-04", 40_000, 500, 0.05)]:
        s.add_spend(day=day, agent=agent, task_id=task, root_id=task, model="claude-sonnet-5-5",
                    usage={"input_tokens": 0, "output_tokens": out, "cache_read_input_tokens": ctx - 4_000,
                           "cache_creation_input_tokens": 4_000}, cost=cost, request_id=None)
    return s


def test_biggest_task_first_with_average_context(store):
    rows = store.spend_by_task()
    assert [r["title"] for r in rows] == ["Big memo", "Re-run model"]
    assert rows[0]["calls"] == 2 and rows[0]["avg_context"] == 122_000
    assert rows[0]["cost"] == pytest.approx(0.70) and rows[0]["output_tokens"] == 5_000


def test_one_day_and_the_row_limit(store):
    assert [r["title"] for r in store.spend_by_task("2026-10-04")] == ["Re-run model"]
    assert len(store.spend_by_task(limit=1)) == 1
