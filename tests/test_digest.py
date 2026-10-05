"""The activity digest: what a wing is doing, read from events by code, with no model call."""

from __future__ import annotations

from conftest import nick, text_turn, tool_turn

from hq.digest import office_digest, person_digest, wing_digest


async def test_digest_reads_a_lead_mid_task_without_a_model_call(make_office):
    office, llm = make_office()
    seen = {}

    def peek(params):   # runs at the lead's second turn, after it delegated
        seen["wing"] = wing_digest(office, "equity_research")
        return text_turn("Done.")

    llm.script("er_lead", tool_turn(("delegate", {"to": "er_associate", "job": "Pull FY facts."})),
               peek)
    llm.script("er_associate", tool_turn(("submit_result", {"findings": "facts", "confidence": "high"})))
    calls_before = len(llm.calls)
    office.assign("er_lead", "Write the memo.", title="Memo")
    await office.idle()
    wing = seen["wing"]
    lead = next(p for p in wing["people"] if p["id"] == "er_lead")
    assert wing["state"] == "working"
    assert lead["working_on"] == "Memo"
    assert any(f"handed a job to {nick('er_associate')}" in line for line in lead["recent"])
    assert nick("er_lead") in wing["headline"]
    assert len(llm.calls) - calls_before == 3   # the digest itself added none


async def test_digest_idle_wing_and_no_costs(make_office):
    office, _ = make_office()
    wing = wing_digest(office, "screening")
    assert wing["state"] == "idle" and "nobody here has work in hand" in wing["headline"]
    assert "$" not in str(office_digest(office))


async def test_digest_shows_blockers_and_waiting_cards(make_office):
    office, llm = make_office()
    llm.script("er_lead", tool_turn(("request_approval", {"kind": "brief", "title": "EOSE brief",
                                                          "summary": "Please review."})),
               text_turn("Waiting."))
    office.assign("er_lead", "Brief.", title="Brief")
    await office.idle()
    wing = wing_digest(office, "equity_research")
    assert wing["waiting_on_captain"] == ["EOSE brief"]
    assert wing["state"] == "waiting"
    office.raise_incident("er_lead", None, "paused", "Paused by Vera: test")
    assert wing_digest(office, "equity_research")["state"] == "blocked"


async def test_digest_works_while_the_office_is_held(make_office):
    office, _ = make_office()
    office.hold()
    d = office_digest(office)
    assert d["held"] and all(w["state"] == "held" for w in d["wings"])
    assert person_digest(office, "er_lead")["working_on"] is None
