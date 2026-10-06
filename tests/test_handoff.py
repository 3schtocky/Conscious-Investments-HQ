"""Departments: the Model Brief and the hand-off to Client Relations. Plain code; no model calls."""

from __future__ import annotations

import asyncio
import json
import shutil
from types import SimpleNamespace

import pytest

from hq import handoff, modelbrief
from hq.tools import brief as brief_tools
from hq.tools import desk
from hq.tools.desk import coverage_dir

META_ASSUMPTIONS = coverage_dir("META") / "assumptions.yaml"
pytestmark = pytest.mark.skipif(not META_ASSUMPTIONS.exists(), reason="needs the META coverage pack")


@pytest.fixture
def ready(make_office, monkeypatch, tmp_path):
    """An office with an approved Outperform model for META, built from real assumptions."""
    root = tmp_path / "erb"
    (root / "coverage" / "META").mkdir(parents=True)
    monkeypatch.setattr(desk, "ERB_DIR", root)
    frozen = tmp_path / "quant" / "META" / "META_model_v1_assumptions.yaml"
    frozen.parent.mkdir(parents=True)
    shutil.copy(META_ASSUMPTIONS, frozen)
    office, llm = make_office()
    office.store.add_model(ticker="META", version=1, path=str(frozen.with_suffix(".xlsx")), created_by="quant_lead",
                           summary={"price_targets": {"bear": 1, "base": 2, "bull": 3}, "total_returns": {},
                                    "rating": "Outperform", "price": 700.0, "warnings": [], "as_of": "2026-09-29",
                                    "target_date": "2027-12-30", "check": {"ok": True},
                                    "assumptions": str(frozen)})
    office.store.set_model_status("META", 1, "approved")
    return office, llm, root


def add_report(root, ticker="META"):
    (root / "coverage" / ticker / f"CI_{ticker}_Initiating-Coverage_2026-10-05.pdf").write_bytes(b"%PDF")


def reading(facts, **over):
    sc = facts["scenarios"]
    names = [d["driver"] for d in facts["drivers"]]
    r = {"view": "The base case clears the benchmark by a wide margin, so the rating follows from the model. "
                 f"Our base target is ${sc['base']['price_target']:,.2f} against the price of ${facts['price']:,.2f}.",
         "drivers": [{"driver": n, "why": "It moves the target more than any other input."} for n in names[:3]],
         "scenarios": {"bear": "Growth slows and the multiple compresses.", "base": "Growth fades as modelled.",
                       "bull": "Growth and margins both beat the plan."},
         "sensitivity_note": "The target is most exposed to the discount rate and the exit multiple.",
         "breaks": ["Revenue growth falls below the bear path for two quarters running."]}
    r.update(over)
    return r


def ctx(office, agent):
    return SimpleNamespace(office=office, agent=office.agents[agent], task={"id": 1})


def test_facts_come_from_the_engine_and_the_reading_may_only_quote_them(ready):
    office, _, _ = ready
    b = modelbrief.ensure_facts(office, "META", 1)
    f = b["facts"]
    sc = f["scenarios"]
    assert sc["bear"]["price_target"] <= sc["base"]["price_target"] <= sc["bull"]["price_target"]
    assert 3 <= len(f["drivers"]) and f["drivers"][0]["swing"] >= f["drivers"][-1]["swing"]
    assert modelbrief.check_reading(f, reading(f)) == []
    bad = modelbrief.check_reading(f, reading(f, view=reading(f)["view"] + " Fair value is $12,345.67."))
    assert any("$12,345.67" in p for p in bad)
    bad = modelbrief.check_reading(f, reading(f, drivers=[{"driver": "Vibes", "why": "x"}] * 3))
    assert any("not one of the model's drivers" in p for p in bad)
    assert modelbrief.check_reading(f, reading(f, breaks=[])) and modelbrief.check_reading(f, {"view": "short"})
    assert modelbrief.status(office, "META")["state"] == "facts"


def test_every_condition_must_hold_before_a_card_is_filed(ready):
    office, _, root = ready
    r = handoff.readiness(office, "META")
    assert not r["ready"] and [c["key"] for c in r["conditions"] if not c["ok"]] == ["report", "brief"]
    assert handoff.maybe_offer(office, "META") is None
    add_report(root)
    f = modelbrief.ensure_facts(office, "META", 1)["facts"]
    modelbrief.save_reading(office, "META", 1, reading(f), by="Sigma")
    assert handoff.readiness(office, "META")["ready"]
    card = handoff.maybe_offer(office, "META")
    assert card and office.store.approval(card)["kind"] == "handoff"
    assert handoff.maybe_offer(office, "META") is None   # one card per approved version
    assert handoff.scan(office) == []
    assert handoff.readiness(office, "META")["handoff"]["approval"] == card


def test_only_outperform_names_qualify(ready):
    office, _, root = ready
    add_report(root)
    f = modelbrief.ensure_facts(office, "META", 1)["facts"]
    modelbrief.save_reading(office, "META", 1, reading(f), by="Sigma")
    office.store._exec("UPDATE models SET summary=? WHERE ticker='META'",
                       (json.dumps({**office.store.approved_model("META")["summary"], "rating": "Neutral"}),))
    r = handoff.readiness(office, "META")
    assert not r["ready"] and not r["conditions"][1]["ok"] and handoff.maybe_offer(office, "META") is None


async def test_approving_the_card_gives_harbor_the_assignment(ready):
    from conftest import text_turn

    office, llm, root = ready
    llm.script("cr_lead", text_turn("Outline ready."))
    add_report(root)
    f = modelbrief.ensure_facts(office, "META", 1)["facts"]
    modelbrief.save_reading(office, "META", 1, reading(f), by="Sigma")
    card = handoff.maybe_offer(office, "META")
    office.decide(card, "approved")
    await office.idle()
    task = next(t for t in office.store.tasks() if t["assignee"] == "cr_lead")
    assert "META" in task["title"] and "relay_request" in task["body"] and task["assigned_by"] == "captain"


def test_a_stale_card_cannot_be_approved_and_a_decline_starts_nothing(ready):
    office, _, root = ready
    add_report(root)
    f = modelbrief.ensure_facts(office, "META", 1)["facts"]
    modelbrief.save_reading(office, "META", 1, reading(f), by="Sigma")
    card = handoff.maybe_offer(office, "META")
    (root / "coverage" / "META" / "CI_META_Initiating-Coverage_2026-10-05.pdf").unlink()
    with pytest.raises(ValueError, match="out of date"):
        office.decide(card, "approved")
    office.decide(card, "rejected")
    assert not [t for t in office.store.tasks() if t["assignee"] == "cr_lead"]


def test_tools_quant_writes_and_client_relations_waits_for_a_finished_brief(ready):
    office, _, root = ready
    from hq.engine.guards import GuardBlock

    with pytest.raises(GuardBlock, match="not ready"):
        asyncio.run(brief_tools._read_model_brief(ctx(office, "cr_lead"), {"ticker": "META"}))
    out = json.loads(asyncio.run(brief_tools._read_model_brief(ctx(office, "quant_lead"), {"ticker": "META"})))
    assert out["state"] == "facts" and out["reading"] is None
    r = reading(out["facts"])
    bad = json.loads(asyncio.run(brief_tools._save_model_brief(ctx(office, "quant_lead"), {"ticker": "META", **r, "breaks": []})))
    assert not bad["saved"] and bad["fix"]
    add_report(root)
    ok = json.loads(asyncio.run(brief_tools._save_model_brief(ctx(office, "quant_lead"), {"ticker": "META", **r})))
    assert ok["saved"] and "hand-off card" in ok["handoff"] and "approval #" in ok["handoff"]
    seen = json.loads(asyncio.run(brief_tools._read_model_brief(ctx(office, "cr_lead"), {"ticker": "META"})))
    assert seen["state"] == "ready" and seen["reading"]["by"]
    assert (office.quant_dir / "META" / "META_model_v1_brief.md").read_text().startswith("# META Model Brief")


def test_the_new_tools_are_on_the_right_desks():
    names = lambda wing, tier: {t.name for t in desk.desk_tools(wing, tier)}
    assert {"save_model_brief", "read_model_brief"} <= names("quant", "associate")
    assert "save_model_brief" not in names("client_relations", "lead")
    assert {"read_model_brief", "slidedeck_readiness"} <= names("client_relations", "associate")
