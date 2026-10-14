"""Model cascade (phase C1): the wing's associate goes first, the lead polishes on a code trigger."""

from __future__ import annotations

import pytest

from hq import cascade, config
from tests.conftest import text_turn, tool_turn

HAIKU_TURN = 0.002    # one USAGE turn at the fake Haiku price: 1000*1 + 200*5 per MTok
SONNET_TURN = 0.004

DONE = {"findings": "Model built.", "figures": [], "open_questions": [], "confidence": "high"}


@pytest.fixture
def office(make_office):
    off, llm = make_office(tiered=True)
    cfg = config.office()["cascade"]
    cfg["enabled"] = True
    cfg["kinds"] = {"model": {"lead": "quant_lead", "match": "model"}}
    return off, llm, cfg


async def go(off, body="Build the model for XYZ."):
    tid = off.assign("quant_lead", body)
    await off.idle()
    return tid


def polish_of(off, tid):
    return next((t for t in off.store.tasks() if t["escalated_from"] == tid), None)


async def test_clean_first_pass_never_wakes_the_lead(office):
    off, llm, _ = office
    llm.script("quant_associate", tool_turn(("submit_result", DONE)))
    tid = await go(off)
    t = off.store.task(tid)
    assert t["assignee"] == "quant_associate" and t["cascade"] == "model" and t["status"] == "done"
    assert polish_of(off, tid) is None and not [c for c in llm.calls if c["who"] == "quant_lead"]
    assert tid in off.reported   # the Captain still hears the result
    assert cascade.by_tier(off.store)["tiers"].keys() == {"associate"}
    assert off.store.spend_for_root(tid) == pytest.approx(HAIKU_TURN)


async def test_failed_tool_calls_hand_the_draft_to_the_lead(office):
    off, llm, _ = office
    llm.script("quant_associate", tool_turn(("nope", {})), tool_turn(("nope", {})),
               tool_turn(("submit_result", {**DONE, "findings": "Draft with a loose figure."})))
    llm.script("quant_lead", text_turn("Fixed and reported."))
    tid = await go(off)
    p = polish_of(off, tid)
    assert p["assignee"] == "quant_lead" and p["attempt"] == 2 and p["escalation_reason"] == "gate_fails"
    assert p["root_id"] == tid and p["status"] == "done" and p["assigned_by"] == "captain"
    assert "Draft with a loose figure." in p["body"] and "Unknown tool 'nope'" in p["body"]
    assert off.store.task(tid)["status_reason"] == "escalated: gate_fails"
    tiers = cascade.by_tier(off.store)["tiers"]
    assert tiers["associate"]["cost"] == pytest.approx(3 * HAIKU_TURN)
    assert tiers["lead"]["cost"] == pytest.approx(SONNET_TURN)
    s = cascade.summary(off.store)
    assert s["haiku_first"] == 1 and s["escalated"] == 1 and s["reasons"] == {"gate_fails": 1}


async def test_one_stumble_is_not_an_escalation(office):
    off, llm, _ = office
    llm.script("quant_associate", tool_turn(("nope", {})), tool_turn(("submit_result", DONE)))
    tid = await go(off)
    assert polish_of(off, tid) is None


async def test_turn_cap_escalates_without_an_incident(office):
    off, llm, cfg = office
    cfg["kinds"]["model"]["max_turns"] = 1
    llm.script("quant_associate", tool_turn(("nope", {})))
    llm.script("quant_lead", text_turn("Took it from here."))
    tid = await go(off)
    assert polish_of(off, tid)["escalation_reason"] == "turn_cap"
    assert off.store.incidents() == []


async def test_first_pass_dollar_cap_escalates(office):
    off, llm, cfg = office
    cfg["kinds"]["model"]["attempt_cap_usd"] = 0.001
    llm.script("quant_associate", tool_turn(("nope", {})))
    llm.script("quant_lead", text_turn("Took it."))
    tid = await go(off)
    assert polish_of(off, tid)["escalation_reason"] == "task_cost_cap"


async def test_lead_review_kinds_always_reach_the_lead_but_do_not_count_as_escalations(office):
    off, llm, cfg = office
    cfg["kinds"]["model"]["lead_review"] = True
    llm.script("quant_associate", tool_turn(("submit_result", DONE)))
    llm.script("quant_lead", text_turn("Reviewed."))
    tid = await go(off)
    assert polish_of(off, tid)["escalation_reason"] == "lead_review"
    s = cascade.summary(off.store)
    assert s["escalated"] == 0 and s["reasons"] == {"lead_review": 1}


async def test_only_matching_assignments_cascade(office):
    off, llm, _ = office
    llm.script("quant_lead", text_turn("Chatting."))
    tid = await go(off, "How is the weather in the Quant Lab?")
    assert off.store.task(tid)["assignee"] == "quant_lead" and off.store.task(tid)["cascade"] is None


async def test_cascade_off_keeps_the_old_flow(office):
    off, llm, cfg = office
    cfg["enabled"] = False
    llm.script("quant_lead", text_turn("Done."))
    tid = await go(off)
    assert off.store.task(tid)["assignee"] == "quant_lead" and not [c for c in llm.calls if c["who"] == "quant_associate"]


async def test_a_tally_flag_on_the_draft_escalates(office):
    off, _, _ = office
    tid = off.store.create_task(assignee="quant_associate", assigned_by="captain", kind="assignment",
                                title="Model", body="x", cascade="model")
    task = off.store.task(tid)
    assert cascade.review(off, task, {"status": "done"}, []) is None
    off.store.add_finding(agent="quant_associate", task_id=tid, root_id=tid, rule="unapproved_figures",
                          severity="flag", subject="XYZ", detail="price target not approved")
    assert cascade.review(off, task, {"status": "done"}, []) == "audit_flag"


async def test_budget_and_api_pauses_wait_instead_of_escalating(office):
    off, _, _ = office
    tid = off.store.create_task(assignee="quant_associate", assigned_by="captain", kind="assignment",
                                title="Model", body="x", cascade="model")
    task = off.store.task(tid)
    assert cascade.review(off, task, {"status": "paused", "reason": "api_off"}, []) is None
    assert cascade.review(off, task, {"status": "paused_budget"}, []) is None
