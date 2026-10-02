"""Pausing the whole office: work waits where it is, and the Captain can still talk to anyone
one to one. Resuming picks everything up where it stopped."""

from __future__ import annotations

import asyncio
import json

from conftest import text_turn, tool_turn

from hq.engine.runtime import Office


def _calls(llm, who):
    return [c for c in llm.calls if c["who"] == who]


def _last_user(call) -> str:
    return json.dumps(call["params"]["messages"][-1]["content"])


async def test_pause_holds_work_and_resume_continues_it(make_office):
    office, llm = make_office()

    def first(params):
        office.hold()                         # the Captain hits pause while this step is in flight
        return tool_turn(("send_message", {"to": ["Ledger"], "text": "Pull the 10-K."}))

    llm.script("Quill", first, text_turn("Memo done."))
    llm.script("Ledger", text_turn("On it."))
    task = office.assign("Quill", "Write the memo")
    await asyncio.sleep(0.05)
    # the step in flight finished (its message was delivered), then everyone stopped
    assert [c["who"] for c in llm.calls] == ["Quill"]
    assert office.agents["er_lead"].status == "held" and office.agents["er_associate"].status == "held"
    assert office.store.task(task)["status"] == "running" and office.snapshot()["office"]["held"]
    assert any(m["text"] == "Pull the 10-K." for m in office.store.chat())
    office.hold()                             # pressing it twice changes nothing
    assert [e["payload"]["held"] for e in office.store.events_of_type("office_hold")] == [True]

    office.release()
    await office.idle()
    assert office.store.task(task)["status"] == "done" and not office.held
    assert sorted(c["who"] for c in llm.calls) == ["Ledger", "Quill", "Quill"]
    assert all(a.status == "idle" for a in office.agents.values())


async def test_the_captain_can_talk_to_a_busy_agent_while_the_office_is_paused(make_office, tmp_path, monkeypatch):
    from hq.tools import desk

    monkeypatch.setattr(desk, "ERB_DIR", tmp_path / "erb")
    office, llm = make_office()

    def first(params):
        office.hold()
        return tool_turn(("read_file", {"ticker": "ZZZZ", "path": "notes/none.md"}))

    llm.script("Quill", first,
               tool_turn(("write_file", {"ticker": "ZZZZ", "path": "brief.md", "content": "carry on regardless"}),
                         ("send_message", {"to": ["Ledger"], "text": "keep going"}),
                         ("report_to_captain", {"text": "My concern: two figures have no source yet."})),
               text_turn("I would hold the memo until they are sourced."),
               text_turn("Memo done."))
    task = office.assign("Quill", "Write the ZZZZ memo")
    await asyncio.sleep(0.05)
    assert len(_calls(llm, "Quill")) == 1

    out = office.captain_send("Quill", "What is your concern with this one?")
    await asyncio.sleep(0.05)
    assert out["delivered"] == "inbox" and len(_calls(llm, "Quill")) == 2      # one message, one turn
    turn = _last_user(_calls(llm, "Quill")[1])
    assert "What is your concern with this one?" in turn and "has paused the whole office" in turn
    # in that turn he may answer and read, and nothing else moves
    results = _calls(llm, "Quill")[1 + 0]["params"]["messages"]   # (the reply's tool results come next turn)
    assert results
    assert not (tmp_path / "erb" / "coverage" / "ZZZZ" / "brief.md").exists()
    assert not any(m["text"] == "keep going" for m in office.store.chat())
    assert office.store.chat("dm:captain|er_lead")[-1]["text"] == "My concern: two figures have no source yet."
    assert office.agents["er_lead"].status == "held" and office.agents["er_associate"].status == "idle"

    office.captain_send("Quill", "And what would you do?")
    await asyncio.sleep(0.05)
    blocked = json.dumps(_calls(llm, "Quill")[2]["params"]["messages"][-1]["content"])
    assert blocked.count("Not run: the office is paused by Stott") == 2          # write_file and send_message
    # a plain-text answer still reaches him
    assert office.store.chat("dm:captain|er_lead")[-1]["text"] == "I would hold the memo until they are sourced."
    assert len(_calls(llm, "Quill")) == 3

    office.release()
    await office.idle()
    assert office.store.task(task)["status"] == "done" and len(_calls(llm, "Quill")) == 4
    assert "tool_loop" not in {i["kind"] for i in office.store.incidents()}


async def test_an_idle_agent_answers_and_office_wide_messages_wait(make_office):
    office, llm = make_office()
    office.hold()
    llm.script("Sigma", text_turn("The bear case rests on margin compression."))
    llm.script("Juno", tool_turn(("assign_task", {"to": "Quill", "title": "NVDA", "brief": "Research NVDA"})),
               text_turn("Routed."))
    llm.script("Quill", text_turn("Starting NVDA."))
    office.captain_send("Sigma", "Walk me through the bear case.")
    office.captain_send("office", "Research NVDA next.")
    await asyncio.sleep(0.05)
    assert [c["who"] for c in llm.calls] == ["Sigma"]                            # Juno waits with the rest
    assert office.store.chat("dm:captain|quant_lead")[-1]["text"] == "The bear case rests on margin compression."
    assert "has paused the whole office" in _last_user(_calls(llm, "Sigma")[0])
    assert office.agents["chief_of_staff"].status == "held"
    office.release()
    await office.idle()
    assert sorted(c["who"] for c in llm.calls) == ["Juno", "Juno", "Quill", "Sigma"]
    assert all(t["status"] == "done" for t in office.store.tasks())


async def test_a_paused_associate_answers_in_plain_text_and_then_finishes_its_job(make_office):
    office, llm = make_office()

    def first(params):
        office.hold()
        return tool_turn(("delegate", {"to": "Ledger", "job": "Pull the facts."}))

    llm.script("Quill", first, text_turn("Memo done."))
    llm.script("Ledger", text_turn("Halfway through the 10-K; nothing worrying so far."),
               tool_turn(("submit_result", {"findings": "Facts pulled.", "confidence": "high"})))
    office.assign("Quill", "Write the memo")
    await asyncio.sleep(0.05)
    office.captain_send("Ledger", "Where are you?")       # an associate has no report tool: text is enough
    await asyncio.sleep(0.05)
    assert office.store.chat("dm:captain|er_associate")[-1]["text"] == "Halfway through the 10-K; nothing worrying so far."
    assert office.store.tasks()[1]["status"] == "running"  # the answer did not end the delegated job
    office.release()
    await office.idle()
    assert [t["status"] for t in office.store.tasks()] == ["done", "done"]
    assert json.loads(office.store.tasks()[1]["result"])["findings"] == "Facts pulled."


async def test_the_pause_survives_a_restart_and_visitors_only_see_that_it_is_paused(make_office):
    from fastapi.testclient import TestClient

    from hq import public as pub
    from hq.server import create_app

    office, llm = make_office()
    with TestClient(create_app(office_factory=lambda: office)) as c:
        assert c.post("/api/office/hold").json() == {"held": True} and office.held
        assert c.get("/api/state").json()["office"]["held"] is True
        again = Office(store=office.store, llm=llm, ledger=office.ledger)
        assert again.held and not any(a.paused for a in again.agents.values())
        assert c.get("/api/audit").json()["paused"] == []                       # not an agent pause
        assert c.post("/api/office/release").json() == {"held": False} and not office.held
        assert not Office(store=office.store, llm=llm, ledger=office.ledger).held
    assert pub.event(office, {"type": "office_hold", "agent": None, "held": True, "by": "x"}) == \
        {"id": None, "ts": None, "type": "office_hold", "agent": None, "task_id": None, "held": True}
    assert pub.event(office, {"type": "status", "agent": "er_lead", "status": "held"})["status"] == "held"
    assert pub.state(office, demo=False)["office"]["held"] is False


async def test_demo_agents_answer_during_a_pause_without_losing_their_script(make_office):
    from hq.demo import DemoLLM, think

    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    office.hold()
    llm.script("er_lead", think("Working.", "Scripted memo text."))
    office.assign("Quill", "Draft a memo (demo)")
    office.captain_send("Quill", "Where are you on this?")
    await asyncio.sleep(0.3)
    assert office.store.chat("dm:captain|er_lead")[-1]["text"].startswith("(demo) Paused where I am on:")
    assert len(llm.scripts["er_lead"]) == 1                                     # the scene's script is intact
    office.release()
    await office.idle()
    assert not llm.scripts["er_lead"] and all(t["status"] == "done" for t in office.store.tasks())
