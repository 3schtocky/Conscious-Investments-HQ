"""Juno's rounds: she walks to each busy wing's delegate, asks where it stands, and relays the
roll-up, live and while the office is paused."""

from __future__ import annotations

import pytest
from conftest import nick, text_turn, tool_turn

from hq import rounds as rounds_mod


@pytest.fixture(autouse=True)
def _no_walking_delay(monkeypatch):
    monkeypatch.setattr(rounds_mod, "settings", lambda: {**rounds_mod.DEFAULTS, "walk_pause_seconds": 0})


async def _er_waiting_on_captain(office, llm) -> None:
    llm.script("er_lead", tool_turn(("request_approval", {"kind": "brief", "title": "EOSE brief",
                                                          "summary": "Please review."})),
               text_turn("Waiting."))
    office.assign("er_lead", "Brief.", title="EOSE brief")
    await office.idle()


async def test_rounds_ask_each_busy_delegate_and_juno_takes_no_turn(make_office):
    office, llm = make_office()
    await _er_waiting_on_captain(office, llm)
    llm.script("comms:er_associate", text_turn("Quill filed the EOSE brief and is waiting on Stott."))
    out = await office.rounds.walk(reason="asked")
    assert out["mode"] == "model" and [w["wing"] for w in out["wings"]] == ["equity_research"]
    assert "waiting on Stott" in out["summary"] and nick("er_associate") in out["summary"]
    assert {c["who"] for c in llm.calls} == {"er_lead", "comms:er_associate"}   # Juno made no call
    moves = [e["payload"]["to"] for e in office.store.events() if e["type"] == "move"]
    assert moves == ["desk:er_associate", "desk:chief_of_staff"]   # walked over, then back home
    assert office.rounds.latest()["summary"] == out["summary"]


async def test_rounds_while_paused_use_the_free_code_roll_up(make_office):
    office, llm = make_office()
    await _er_waiting_on_captain(office, llm)
    calls = len(llm.calls)
    office.hold()
    out = await office.rounds.walk(reason="asked")
    assert out["mode"] == "code" and out["held"] and len(llm.calls) == calls
    assert out["summary"].startswith("Rounds while the office is paused")
    assert "EOSE brief" in out["summary"]   # read from the live log, not guessed


async def test_idle_office_has_nothing_to_walk(make_office):
    office, llm = make_office()
    out = await office.rounds.walk(reason="asked")
    assert out["wings"] == [] and "every wing is idle" in out["summary"] and llm.calls == []
    assert await office.rounds.tick() is None


async def test_timer_posts_on_change_and_heartbeat_but_not_every_tick(make_office):
    office, llm = make_office()
    await _er_waiting_on_captain(office, llm)
    for _ in range(4):
        llm.script("comms:er_associate", text_turn("Still waiting on Stott."))
    posted = []
    orig = office.report_to_captain

    async def spy(sender, text, task_id=None):
        posted.append(text)
        await orig(sender, text, task_id)

    office.report_to_captain = spy
    await office.rounds.tick()          # first sight of this state: posts
    await office.rounds.tick()          # nothing changed: board only
    assert len(posted) == 1
    await office.rounds.tick()          # every third round is a heartbeat
    assert len(posted) == 2
    await office.rounds.tick()          # and then quiet again
    assert len(posted) == 2
    assert len(office.store.events_where(types=["rounds"], limit=10)) == 4   # the board updated each time


async def test_paused_timer_walks_only_when_something_changed(make_office):
    office, llm = make_office()
    await _er_waiting_on_captain(office, llm)
    office.hold()
    first = await office.rounds.tick()
    assert first and first["mode"] == "code"
    assert await office.rounds.tick() is None   # still the same: no repeat while paused
    office.raise_incident("er_lead", None, "paused", "Paused by Vera: test")
    assert await office.rounds.tick() is not None   # a new blocker is news


async def test_juno_walks_the_floor_on_request_even_when_paused(make_office):
    office, llm = make_office()
    await _er_waiting_on_captain(office, llm)
    office.hold()
    llm.script("chief_of_staff", tool_turn(("walk_the_floor", {})),
               text_turn("Research is waiting on you for the EOSE brief."))
    office.captain_send("chief_of_staff", "Where is everyone?")
    await office.idle()
    juno_calls = [c for c in llm.calls if c["who"] == "chief_of_staff"]
    res = juno_calls[1]["params"]["messages"][-1]["content"][0]
    assert not res.get("is_error") and "EOSE brief" in res["content"]
    assert not [c for c in llm.calls if c["who"].startswith("comms:")]   # no model turn for the roll-up


def test_rounds_endpoints(make_office):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, _ = make_office()
    office.tone_engine = "rules"
    with TestClient(create_app(office_factory=lambda: office)) as c:
        r = c.get("/api/rounds").json()
        assert r["last_round"] is None and {w["wing"] for w in r["digest"]["wings"]} >= {"quant"}
        walked = c.post("/api/rounds/walk").json()
        assert "every wing is idle" in walked["summary"]


async def test_demo_delegates_answer_juno_from_the_live_digest(make_office):
    from hq.demo import DemoLLM

    office, _ = make_office()
    office._llm = DemoLLM(speed=1000, office=office)
    await _er_waiting_on_captain_demo(office)
    out = await office.rounds.walk(reason="asked")
    assert out["mode"] == "model" and "EOSE brief" in out["summary"]
    assert not [t for t in office.store.tasks() if t["assignee"] == "chief_of_staff"]   # nothing woke Juno


async def _er_waiting_on_captain_demo(office) -> None:
    office.request_approval("er_lead", kind="brief", title="EOSE brief", summary="Please review.",
                            task_id=None, payload={})
