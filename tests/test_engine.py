"""Agent loop, messaging, delegation and control, against the scripted fake model."""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import TURN_COST, nick, text_turn, tool_turn


async def test_simple_task_completes_and_is_logged(make_office):
    office, llm = make_office()
    llm.script("er_lead", text_turn("Done: here is my recap."))
    events = office.bus.subscribe()
    tid = office.assign("er_lead", "Say hello to the Captain.")
    await office.idle()

    task = office.store.task(tid)
    assert task["status"] == "done" and task["result"] == "Done: here is my recap."
    assert office.store.spend_for_task(tid) == pytest.approx(TURN_COST)
    system, messages = office.store.conversation(tid)
    assert "You are **Quill**" in system and "Conscious Investments" in system
    assert [m["role"] for m in messages] == ["user", "assistant"]
    types = []
    while not events.empty():
        types.append(events.get_nowait().type)
    for t in ("task_created", "task_started", "thinking_delta", "thinking", "text", "spend",
              "task_done"):
        assert t in types
    # Captain's assignment is in the chat log
    assert office.store.chat("dm:captain|er_lead")[0]["text"] == "Say hello to the Captain."


async def test_request_params_per_model_family():
    from hq.engine.llm import request_params

    sonnet = request_params({"id": "claude-sonnet-5-5", "effort": "high",
                             "thinking_display": "summarized", "max_tokens": 32000},
                            system="s", messages=[], tools=[{"name": "t"}])
    assert sonnet["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sonnet["output_config"] == {"effort": "high"}
    assert sonnet["cache_control"] == {"type": "ephemeral"}
    haiku = request_params({"id": "claude-haiku-4-5", "thinking_budget_tokens": 2048,
                            "max_tokens": 16000}, system="s", messages=[], tools=[])
    assert haiku["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert "output_config" not in haiku and "tools" not in haiku
    no_think = request_params({"id": "claude-haiku-4-5", "thinking_budget_tokens": 0},
                              system="s", messages=[], tools=[])
    assert "thinking" not in no_think


def test_block_to_param_keeps_everything_the_api_returned():
    from hq.engine.llm import block_to_param

    assert block_to_param({"type": "text", "text": "hi", "citations": None,
                           "parsed_output": {}}) == {"type": "text", "text": "hi"}
    assert block_to_param({"type": "thinking", "thinking": "t", "signature": "s"}) == \
        {"type": "thinking", "thinking": "t", "signature": "s"}
    # Regression (first live run): a web search made from inside code execution is linked to
    # that code run by `caller`; dropping it made the API reject the replayed turn.
    nested = {"type": "server_tool_use", "id": "srvtoolu_2", "name": "web_search",
              "input": {"query": "RMBS"}, "caller": {"type": "code_execution_20260120",
                                                    "tool_id": "srvtoolu_1"}}
    assert block_to_param(nested) == nested
    result = {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_2", "content": [],
              "caller": {"type": "code_execution_20260120", "tool_id": "srvtoolu_1"}}
    assert block_to_param(result) == result


async def test_message_to_idle_colleague_becomes_their_task(make_office):
    office, llm = make_office()
    llm.script("chief_of_staff",
               tool_turn(("send_message", {"to": ["screen_lead"], "text": "Any semis on your list?"})),
               text_turn("Asked Scout."))
    llm.script("screen_lead", text_turn("Replied to nobody; noted."))
    await _run(office, "chief_of_staff", "Coordinate with Screening.")

    chat = office.store.chat("dm:chief_of_staff|screen_lead")
    assert chat[-1]["text"] == "Any semis on your list?"
    scout_tasks = [t for t in office.store.tasks() if t["assignee"] == "screen_lead"]
    assert len(scout_tasks) == 1 and scout_tasks[0]["kind"] == "message"
    assert "Any semis on your list?" in scout_tasks[0]["body"]
    assert scout_tasks[0]["status"] == "done"
    # cross-wing message: Juno walked over and back
    moves = [e for e in office.store.events() if e["type"] == "move"]
    assert [m["payload"]["to"] for m in moves] == ["desk:screen_lead", "desk:chief_of_staff"]


async def test_message_to_busy_colleague_lands_in_next_turn(make_office):
    # Scout is mid-task when Quill's message arrives: it shows up in Scout's next turn.
    office2, llm2 = make_office()
    arrived = asyncio.Event()

    async def scout_waits(params):
        await arrived.wait()
        return tool_turn(("send_message", {"to": ["screen_associate"], "text": "Check EOSE catalysts."}))

    llm2.script("screen_lead", scout_waits, text_turn("Got Quill's note, wrapping up."))
    llm2.script("screen_associate", text_turn("On it."))
    llm2.script("chief_of_staff", tool_turn(("send_message", {"to": ["screen_lead"], "text": "Need RMBS?"})),
                text_turn("Sent."))
    office2.assign("screen_lead", "Run the gem hunt.")
    await asyncio.sleep(0)
    office2.assign("chief_of_staff", "Ping Scout.")
    await asyncio.sleep(0.05)
    arrived.set()
    await office2.idle()
    scout_call = [c for c in llm2.calls if c["who"] == "screen_lead"][-1]
    last_user = scout_call["params"]["messages"][-1]["content"]
    texts = [b.get("text", "") for b in last_user if b["type"] == "text"]
    assert any(f"[Message from {nick('chief_of_staff')} (chief_of_staff)]: Need RMBS?" in t for t in texts)


async def test_delegation_returns_structured_result(make_office):
    office, llm = make_office()
    llm.script("er_lead",
               tool_turn(("delegate", {"to": "er_associate", "job": "Get RMBS FY24 revenue."})),
               lambda params: text_turn("RMBS revenue noted."))
    llm.script("er_associate", tool_turn(("submit_result", {
        "findings": "FY24 revenue found.",
        "figures": [{"label": "Revenue FY24", "value": 556.6, "unit": "USD m",
                     "period": "FY24", "source": "10-K 2024"}],
        "open_questions": [], "confidence": "high"})))
    tid = await _run(office, "er_lead", "Research RMBS.")

    assert office.store.task(tid)["status"] == "done"
    child = next(t for t in office.store.tasks() if t["kind"] == "delegation")
    assert child["assignee"] == "er_associate" and child["parent_id"] == tid
    assert child["root_id"] == tid and child["status"] == "done"
    # the lead saw the associate's JSON as its tool result
    quill_second = [c for c in llm.calls if c["who"] == "er_lead"][1]["params"]["messages"]
    result = json.loads(quill_second[-1]["content"][0]["content"])
    assert result["figures"][0]["value"] == 556.6 and result["confidence"] == "high"
    assert result["delegation_cost_usd"] == pytest.approx(TURN_COST)
    assert "notes" not in result   # no text alongside submit_result
    # root spend includes the delegation
    assert office.store.spend_for_root(tid) == pytest.approx(3 * TURN_COST)


async def test_text_written_with_submit_result_travels_as_notes(make_office):
    office, llm = make_office()
    llm.script("er_lead", tool_turn(("delegate", {"to": "er_associate", "job": "List five checks."})),
               text_turn("Thanks."))
    llm.script("er_associate", tool_turn(("submit_result", {"findings": "Five checks listed.",
                                                      "confidence": "high"}),
                                   text="1. Customers\n2. Margins\n3. R&D\n4. Inventory\n5. Capex"))
    await _run(office, "er_lead", "x")
    msgs = [c for c in llm.calls if c["who"] == "er_lead"][1]["params"]["messages"]
    result = json.loads(msgs[-1]["content"][0]["content"])
    assert result["notes"].startswith("1. Customers")


async def test_delegation_outside_wing_or_to_lead_is_refused(make_office):
    office, llm = make_office()
    llm.script("er_lead",
               tool_turn(("delegate", {"to": "screen_associate", "job": "x"}),
                         ("delegate", {"to": "screen_lead", "job": "y"})),
               text_turn("OK, I'll do it myself."))
    tid = await _run(office, "er_lead", "Research.")
    results = [c for c in llm.calls if c["who"] == "er_lead"][1]["params"]["messages"][-1]["content"]
    assert all(r["is_error"] for r in results)
    assert "another wing" in results[0]["content"] and "not an associate" in results[1]["content"]
    assert office.store.task(tid)["status"] == "done"


async def test_budget_cap_finishes_inflight_then_clocks_out(make_office):
    office, llm = make_office(daily_cap=0.006, audit_reserve=0.0)
    llm.script("chief_of_staff", tool_turn(("send_message", {"to": ["er_lead"], "text": "hi"})),
               tool_turn(("send_message", {"to": ["er_lead"], "text": "a second, different note"})),
               text_turn("done"))
    llm.script("er_lead", text_turn("ok"), text_turn("ok again"))
    tid = await _run(office, "chief_of_staff", "Work.")
    # Call 1 ($0.004) is under the cap, so call 2 starts; it finishes and is recorded even
    # though it takes the day to $0.008. After that nothing new starts: Quill and Ledger's
    # queued message tasks all pause on the budget.
    assert office.store.task(tid)["status"] == "paused_budget"
    assert office.clocked_out
    assert office.ledger.spent_today() == pytest.approx(2 * TURN_COST)
    assert len(llm.calls) == 2

    # Raising the cap resumes from the saved, append-only conversation.
    _, before = office.store.conversation(tid)
    office.ledger.daily_cap = 1.0
    resumed = office.resume_budget_paused()
    assert resumed[0] == tid and len(resumed) == 3   # Quill + Ledger's two queued messages
    await office.idle()
    assert all(office.store.task(t)["status"] == "done" for t in resumed)
    assert not office.clocked_out
    _, after = office.store.conversation(tid)
    assert after[: len(before)] == before


async def test_audit_can_spend_the_reserve(make_office):
    office, llm = make_office(daily_cap=0.006, audit_reserve=0.004)
    llm.script("er_lead", text_turn("one"))
    llm.script("audit_lead", text_turn("audit alert"))
    await _run(office, "er_lead", "a")          # spends 0.004 -> at cap minus reserve
    blocked = await _run(office, "screen_lead", "b")   # non-audit: blocked
    assert office.store.task(blocked)["status"] == "paused_budget"
    ok = await _run(office, "audit_lead", "c")      # audit: may use the reserve
    assert office.store.task(ok)["status"] == "done"


async def test_turn_cap_pauses_task_with_incident(make_office):
    office, llm = make_office()
    llm.script("er_lead", *[tool_turn(("send_message", {"to": ["er_associate"],
                                                      "text": f"update number {i} " + "x" * i * 30}))
                          for i in range(5)])
    llm.script("er_associate", *[text_turn("ack")] * 5)
    import hq.engine.agent as agent_mod
    cfg = agent_mod.office()
    cfg["limits"]["max_turns_per_task"] = 3
    tid = await _run(office, "er_lead", "Chatter.")
    task = office.store.task(tid)
    assert task["status"] == "paused" and task["status_reason"].startswith("turn_cap")
    assert office.store.incidents()[0]["kind"] == "turn_cap"


async def test_identical_tool_calls_trip_loop_guard(make_office):
    office, llm = make_office()
    same = ("delegate", {"to": "er_associate", "job": "same job"})
    llm.script("er_lead", tool_turn(same), tool_turn(same), tool_turn(same), text_turn("x"))
    llm.script("er_associate", *[text_turn("did it")] * 3)
    tid = await _run(office, "er_lead", "Loop.")
    task = office.store.task(tid)
    assert task["status"] == "paused" and task["status_reason"].startswith("tool_loop")


async def test_pause_waits_and_only_captain_resumes(make_office):
    office, llm = make_office()
    llm.script("er_lead", text_turn("finished"))
    office.pause("er_lead", by="audit_lead", reason="checking sourcing")
    tid = office.assign("er_lead", "Work.")
    await asyncio.sleep(0.05)
    assert office.store.task(tid)["status"] == "running" and llm.calls == []
    assert office.store.incidents()[0]["kind"] == "paused"
    with pytest.raises(PermissionError):
        office.resume("er_lead", by="audit_lead")
    office.resume("er_lead", by="captain")
    await office.idle()
    assert office.store.task(tid)["status"] == "done"


async def test_refusal_declines_task_with_incident(make_office):
    office, llm = make_office()
    turn = text_turn("", thinking=None, stop="refusal")
    turn.stop_details = {"category": "general_harms"}
    llm.script("er_lead", turn)
    tid = await _run(office, "er_lead", "Something.")
    assert office.store.task(tid)["status"] == "declined"
    assert office.store.incidents()[0]["kind"] == "refusal"


async def test_pause_turn_continues_without_tool_results(make_office):
    office, llm = make_office()
    llm.script("er_lead", text_turn("searching…", stop="pause_turn"), text_turn("final"))
    tid = await _run(office, "er_lead", "Search.")
    assert office.store.task(tid)["result"] == "final"
    second = [c for c in llm.calls if c["who"] == "er_lead"][1]["params"]["messages"]
    assert second[-1]["role"] == "assistant"


async def test_unknown_colleague_and_bad_input_are_tool_errors(make_office):
    office, llm = make_office()
    llm.script("er_lead",
               tool_turn(("send_message", {"to": ["Bob"], "text": "hi"}),
                         ("send_message", {"to": ["er_associate"]}),
                         ("no_such_tool", {})),
               text_turn("ok"))
    await _run(office, "er_lead", "x")
    results = [c for c in llm.calls if c["who"] == "er_lead"][1]["params"]["messages"][-1]["content"]
    assert [r.get("is_error") for r in results] == [True, True, True]
    assert "No colleague called 'Bob'" in results[0]["content"]


async def test_system_prompt_frozen_while_roster_changes(make_office):
    office, llm = make_office()
    seen = asyncio.Event()

    async def first(params):
        seen.set()
        await asyncio.sleep(0.02)
        return tool_turn(("send_message", {"to": ["er_associate"], "text": "hi"}))

    llm.script("er_lead", first, text_turn("done"))
    llm.script("er_associate", text_turn("ok"))
    tid = office.assign("er_lead", "x")
    await seen.wait()
    office.agents["er_lead"].nickname = "Quincy"   # edited mid-task
    await office.idle()
    systems = {c["params"]["system"] for c in llm.calls if c["who"] == "er_lead"}
    assert len(systems) == 1 and office.store.task(tid)["status"] == "done"


async def _run(office, who: str, body: str) -> int:
    tid = office.assign(who, body)
    await office.idle()
    return tid


# Regression tests for the Phase 1 code review ---------------------------------------------
async def test_truncated_reply_continues_instead_of_finishing(make_office):
    office, llm = make_office()
    llm.script("er_lead", text_turn("The first half of a long", stop="max_tokens"),
               text_turn("…second half. Done."))
    tid = await _run(office, "er_lead", "Write something long.")
    assert office.store.task(tid)["result"] == "…second half. Done."
    last_user = [c for c in llm.calls if c["who"] == "er_lead"][1]["params"]["messages"][-1]
    assert "cut off" in last_user["content"][-1]["text"]


async def test_truncated_tool_call_is_not_run(make_office):
    office, llm = make_office()
    turn = tool_turn(("send_message", {"to": ["er_associate"], "text": "partial"}))
    turn.stop_reason = "max_tokens"
    llm.script("er_lead", turn, text_turn("ok"))
    await _run(office, "er_lead", "x")
    # The cut-off call never reached Ledger; the only reply is Quill's own answer to the Captain.
    assert [(m["sender"], m["text"]) for m in office.store.chat()
            if m["recipients"] != ["er_lead"]] == [("er_lead", "ok")]
    results = [c for c in llm.calls if c["who"] == "er_lead"][1]["params"]["messages"][-1]
    assert results["content"][0]["is_error"] is True


async def test_resume_twice_runs_task_once(make_office):
    office, llm = make_office(daily_cap=0.001, audit_reserve=0.0)
    llm.script("er_lead", text_turn("first"))
    tid = await _run(office, "er_lead", "x")          # first call exceeds cap, finishes
    assert office.store.task(tid)["status"] == "done"
    llm.script("screen_lead", text_turn("resumed"))
    blocked = await _run(office, "screen_lead", "y")
    assert office.store.task(blocked)["status"] == "paused_budget"
    office.ledger.daily_cap = 1.0
    assert office.resume_budget_paused() == [blocked]
    assert office.resume_budget_paused() == []      # already queued: not scheduled again
    await office.idle()
    assert sum(1 for c in llm.calls if c["who"] == "screen_lead") == 1


def test_explicit_model_id_keeps_associate_tier():
    from hq.config import model_config

    assert model_config("associate")[0] == "associate"
    tier, cfg = model_config("claude-haiku-4-5")
    assert tier == "associate" and cfg["thinking_budget_tokens"] == 2048
    tier, cfg = model_config("claude-opus-5-5")
    assert tier == "lead" and cfg["id"] == "claude-opus-5-5" and cfg["effort"] == "high"
    assert model_config("claude-opus-5-5", tier="associate")[0] == "associate"


async def test_loop_guard_trips_before_any_call_in_the_turn_runs(make_office):
    office, llm = make_office()
    same = ("delegate", {"to": "er_associate", "job": "same"})
    llm.script("er_lead", tool_turn(same), tool_turn(same),
               tool_turn(("send_message", {"to": ["screen_lead"], "text": "fresh news"}), same))
    llm.script("er_associate", text_turn("a"), text_turn("b"))
    tid = await _run(office, "er_lead", "x")
    assert office.store.task(tid)["status"] == "paused"
    assert not office.store.chat("dm:er_lead|screen_lead")   # the message in that turn never sent
