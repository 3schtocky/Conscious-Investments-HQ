"""Delegates are the wing's head of communication: leads talk only to them, and the delegates
talk to each other, answer for the wing and relay work, while leads keep building."""

from __future__ import annotations

import json

from conftest import nick, text_turn, tool_turn

from hq.digest import wing_digest


def _results(llm, who: str, call: int = 1) -> list[dict]:
    return [c for c in llm.calls if c["who"] == who][call]["params"]["messages"][-1]["content"]


async def test_lead_may_only_message_its_own_delegate_juno_or_audit(make_office):
    office, llm = make_office()
    llm.script("er_lead",
               tool_turn(("send_message", {"to": ["quant_lead"], "text": "Build the model."}),
                         ("send_message", {"to": ["quant_associate"], "text": "Tell Quant."}),
                         ("send_message", {"to": ["chief_of_staff"], "text": "FYI for Juno."}),
                         ("send_message", {"to": ["audit_lead"], "text": "Please spot-check."})),
               text_turn("Done."))
    llm.script("comms:er_associate", text_turn("Noted."))
    llm.script("chief_of_staff", text_turn("ok"))
    llm.script("audit_lead", text_turn("ok"))
    office.assign("er_lead", "Work.")
    await office.idle()
    res = _results(llm, "er_lead")
    assert [bool(r.get("is_error")) for r in res] == [True, True, False, False]
    assert f"Leads talk to their own delegate. Tell {nick('er_associate')}" in res[0]["content"]


async def test_delegate_may_not_message_another_wings_lead(make_office):
    office, llm = make_office()
    llm.script("comms:er_associate",
               tool_turn(("send_message", {"to": ["quant_lead"], "text": "Build it."})),
               text_turn("Used the wrong tool."))
    office.comms.handle("er_associate", "er_lead", "Quant should build the model.")
    await office.idle()
    first = _results(llm, "comms:er_associate", 1)
    assert first[0]["is_error"] and "relay_request" in first[0]["content"]


async def test_a_message_to_a_delegate_is_answered_on_the_comms_desk_beside_its_job(make_office):
    office, llm = make_office()
    seen = {}

    def during_job(params):   # the lead's job is running at Ledger's desk when the message lands
        seen["busy"] = office.agents["er_associate"].desk.locked()
        return tool_turn(("submit_result", {"findings": "facts", "confidence": "high"}))

    llm.script("er_lead",
               tool_turn(("delegate", {"to": "er_associate", "job": "Pull the facts."})),
               text_turn("Done."))
    llm.script("er_associate", during_job)
    llm.script("comms:er_associate", text_turn("Noted, passing it on."))
    task = office.assign("er_lead", "Memo.")
    await office.idle()
    await office.send_message("er_lead", ["er_associate"], "Model v2 is written.")
    await office.idle()
    kinds = {t["kind"] for t in office.store.tasks() if t["assignee"] == "er_associate"}
    assert kinds == {"delegation", "comms"} and seen["busy"] and task
    comms = next(t for t in office.store.tasks() if t["kind"] == "comms")
    assert comms["status"] == "done" and "Model v2" in comms["body"]
    assert office.store.open_tasks() == []   # comms never clutters the unfinished work list


async def test_ask_delegate_answers_from_the_live_digest(make_office):
    office, llm = make_office()
    llm.script("comms:quant_associate", lambda p: text_turn("Sigma is idle; nothing is in progress."))
    llm.script("chief_of_staff",
               tool_turn(("ask_delegate", {"to": "quant_associate", "question": "Where is Quant?"})),
               text_turn("Quant is idle."))
    office.assign("chief_of_staff", "Where is Quant?")
    await office.idle()
    res = _results(llm, "chief_of_staff")
    assert res[0]["content"] == "Sigma is idle; nothing is in progress."
    system = next(c for c in llm.calls if c["who"] == "comms:quant_associate")["params"]["system"]
    assert "Your wing right now" in system and "Quant" in system
    dm = office.store.chat("dm:chief_of_staff|quant_associate")
    assert [m["sender"] for m in dm] == ["chief_of_staff", "quant_associate"]   # both lines on the floor


async def test_ask_delegate_reads_out_the_digest_with_no_model_call_when_paused(make_office):
    office, llm = make_office()
    office.hold()
    answer = await office.comms.ask("quant_associate", "chief_of_staff", "Where is Quant?")
    assert answer.startswith("Quant:") and "nobody here has work in hand" in answer
    assert llm.calls == []   # a paused office takes no model turn


async def test_relay_request_becomes_an_assignment_for_the_other_lead(make_office):
    office, llm = make_office()
    llm.script("comms:er_associate",
               tool_turn(("relay_request", {"to": "Quant", "need": "Build the RMBS model",
                                            "why": "Thesis is ready.",
                                            "deliverable": "An approval card for Stott."}),
                         ("relay_request", {"to": "Quant", "need": "Build the RMBS model",
                                            "deliverable": "An approval card."}),
                         ("relay_request", {"to": "equity_research", "need": "x",
                                            "deliverable": "y"})),
               text_turn("Sent."))
    llm.script("quant_lead", text_turn("On it."))
    office.comms.handle("er_associate", "er_lead", "RMBS assumptions are ready for Quant.")
    await office.idle()
    task = next(t for t in office.store.tasks() if t["assignee"] == "quant_lead")
    assert task["assigned_by"] == "er_associate" and "Deliverable: An approval card" in task["body"]
    assert nick("quant_associate") in task["body"]   # the result comes back through the delegate
    res = _results(llm, "comms:er_associate")
    assert [bool(r.get("is_error")) for r in res] == [False, True, True]   # duplicate, own wing


async def test_announcements_go_to_delegates_and_leads_only_hear_them(make_office):
    office, llm = make_office()
    office.captain_send("all", "Priorities change on Monday.")
    for d in ("er_associate", "screen_associate", "quant_associate", "cr_associate"):
        llm.script(f"comms:{d}", tool_turn(("post_to_group", {"group": "stott", "text": f"{d} ok"})),
                   text_turn("Posted."))
    for a in ("chief_of_staff", "audit_lead", "audit_associate"):
        llm.script(a, text_turn("ok"))
    await office.idle()
    thread = office.store.chat("group:stott")
    assert not any(m["sender"] in ("er_lead", "screen_lead", "quant_lead", "cr_lead") for m in thread)
    assert {m["sender"] for m in thread[1:]} >= {"er_associate", "quant_associate"}
    # the lead is not woken, but hears it with its next assignment
    assert not [t for t in office.store.tasks() if t["assignee"] == "er_lead"]
    llm.script("er_lead", text_turn("Understood."))
    office.assign("er_lead", "Write the memo.")
    await office.idle()
    first = next(c for c in llm.calls if c["who"] == "er_lead")["params"]["messages"][0]["content"]
    assert "Office notes since you were last at work" in first and "Priorities change" in first


async def test_comms_failure_falls_back_to_the_inbox(make_office):
    office, llm = make_office(daily_cap=0.0001, audit_reserve=0.0)
    office.ledger.record(agent="er_lead", task_id=None, root_id=None, model="fake",
                         usage={"input_tokens": 1000, "output_tokens": 200,
                                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})
    await office.send_message("er_lead", ["er_associate"], "Model v2 is written.")
    await office.idle()
    queued = [t for t in office.store.tasks() if t["assignee"] == "er_associate"]
    assert llm.calls == [] and all("Model v2" in t["body"] for t in queued)


def test_wing_digest_is_in_comms_tool_output(make_office):
    office, _ = make_office()
    from hq.tools.comms import comms_tools

    names = [t.name for t in comms_tools()]
    assert names == ["wing_status", "ask_delegate", "send_message", "relay_request", "post_to_group"]
    assert "ask_delegate" not in [t.name for t in comms_tools(depth=1)]
    assert json.dumps(wing_digest(office, "quant"))
