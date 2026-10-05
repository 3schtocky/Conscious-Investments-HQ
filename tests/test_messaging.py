"""Messages reach the people they are for, replies reach the Captain, and stuck work restarts."""

from __future__ import annotations

import json

import pytest
from conftest import nick, text_turn, tool_turn

from hq.engine.llm import api_problem


async def _run(office, who: str, body: str) -> int:
    task_id = office.assign(who, body)
    await office.idle()
    return task_id


def _result_of(llm, who: str, call_index: int = 1) -> str:
    """The text of the first tool result the agent saw on its `call_index`-th model call."""
    calls = [c for c in llm.calls if c["who"] == who]
    return calls[call_index]["params"]["messages"][-1]["content"][0]["content"]


# Juno's tools ---------------------------------------------------------------------------------
async def test_assign_task_all_leads_reaches_every_lead(make_office):
    office, llm = make_office()
    llm.script("chief_of_staff", tool_turn(("assign_task", {"to": "all_leads", "title": "Focus on EOSE",
                                                  "brief": "Only EOSE work until further notice."})),
               text_turn("Done."))
    for lead in ("er_lead", "screen_lead", "quant_lead", "audit_lead", "cr_lead"):
        llm.script(lead, text_turn("Understood."))
    await _run(office, "chief_of_staff", "Everyone: only EOSE.")
    got = {t["assignee"] for t in office.store.tasks() if t["assigned_by"] == "chief_of_staff"}
    assert got == {"er_lead", "screen_lead", "quant_lead", "audit_lead", "cr_lead"}
    assert "Assigned to Quill" in _result_of(llm, "chief_of_staff")


async def test_assign_task_takes_a_list_and_refuses_associates(make_office):
    office, llm = make_office()
    llm.script("chief_of_staff",
               tool_turn(("assign_task", {"to": ["er_lead", "quant_lead"], "title": "T", "brief": "B"}),
                         ("assign_task", {"to": ["er_associate"], "title": "T2", "brief": "B2"})),
               text_turn("Done."))
    llm.script("er_lead", text_turn("ok"))
    llm.script("quant_lead", text_turn("ok"))
    await _run(office, "chief_of_staff", "Start.")
    out = llm.calls[1]["params"]["messages"][-1]["content"]
    assert "Quill (task #" in out[0]["content"] and "Sigma (task #" in out[0]["content"]
    assert out[1].get("is_error") and "associate" in out[1]["content"]


async def test_read_office_shows_who_does_what_and_what_is_stuck(make_office):
    office, llm = make_office()
    office.store.create_task(assignee="er_lead", assigned_by="captain", kind="assignment",
                             title="Stuck thing", body="x")
    office.store.set_task_status(1, "paused", reason="tool_loop: looped")
    office.request_approval("quant_lead", kind="other", title="A card", summary="s", payload={},
                            task_id=None)
    llm.script("chief_of_staff", tool_turn(("read_office", {})), text_turn("Looked."))
    await _run(office, "chief_of_staff", "What is going on?")
    report = json.loads(_result_of(llm, "chief_of_staff"))
    assert {c["name"] for c in report["colleagues"]} >= {nick("er_lead"), nick("quant_lead"), nick("chief_of_staff")}
    assert report["unfinished_work"][0]["title"] == "Stuck thing"
    assert report["unfinished_work"][0]["why"].startswith("tool_loop")
    assert report["waiting_on_captain"][0]["title"] == "A card"
    assert "cost" not in json.dumps(report).lower()


# replies reach the Captain ----------------------------------------------------------------------
async def test_plain_answer_to_the_captain_is_delivered(make_office):
    office, llm = make_office()
    llm.script("chief_of_staff", text_turn("Quill is on EOSE; Sigma is building the model."))
    tid = await _run(office, "chief_of_staff", "What's going on?")
    reply = [m for m in office.store.chat("dm:captain|chief_of_staff") if m["sender"] == "chief_of_staff"]
    assert [m["text"] for m in reply] == ["Quill is on EOSE; Sigma is building the model."]
    assert reply[0]["task_id"] == tid


async def test_an_agent_that_already_reported_is_not_repeated(make_office):
    office, llm = make_office()
    llm.script("chief_of_staff", tool_turn(("report_to_captain", {"text": "All quiet."})),
               text_turn("Reported."))
    await _run(office, "chief_of_staff", "Status?")
    reply = [m for m in office.store.chat("dm:captain|chief_of_staff") if m["sender"] == "chief_of_staff"]
    assert [m["text"] for m in reply] == ["All quiet."]


async def test_colleague_recaps_are_not_forwarded_to_the_captain(make_office):
    office, llm = make_office()
    llm.script("er_lead", text_turn("Recap for the lead."))
    office.assign("er_lead", "Do a thing.", by="chief_of_staff")
    await office.idle()
    assert [m for m in office.store.chat("dm:captain|er_lead") if m["sender"] == "er_lead"] == []


# stuck work restarts ----------------------------------------------------------------------------
async def _loop_then_finish(office, llm):
    same = ("delegate", {"to": "er_associate", "job": "same job"})
    llm.script("er_lead", tool_turn(same), tool_turn(same), tool_turn(same))
    llm.script("er_associate", *[text_turn("did it")] * 3)
    return await _run(office, "er_lead", "Loop.")


async def test_a_looping_task_can_be_resumed_with_fresh_limits(make_office):
    office, llm = make_office()
    tid = await _loop_then_finish(office, llm)
    assert office.store.task(tid)["status_reason"].startswith("tool_loop")
    incident = office.store.incidents()[0]
    llm.script("er_lead", text_turn("Changed approach and finished."))
    assert office.resume_from_incident(incident["id"]) == [tid]
    await office.idle()
    assert office.store.task(tid)["status"] == "done"
    assert office.store.incidents() == []   # the incident closed with the resume


async def test_resume_refuses_what_cannot_continue(make_office):
    office, _ = make_office()
    t = office.store.create_task(assignee="er_lead", assigned_by="captain", kind="assignment",
                                 title="Long", body="x")
    office.store.set_task_status(t, "paused", reason="context_cap: too long")
    with pytest.raises(ValueError, match="too long"):
        office.resume_task(t)
    d = office.store.create_task(assignee="er_associate", assigned_by="er_lead", kind="delegation",
                                 title="Job", body="x")
    office.store.set_task_status(d, "paused", reason="turn_cap: limit")
    with pytest.raises(ValueError, match="delegated job"):
        office.resume_task(d)
    done = office.store.create_task(assignee="er_lead", assigned_by="captain", kind="assignment",
                                    title="Done", body="x")
    office.store.set_task_status(done, "done")
    with pytest.raises(ValueError, match="not paused"):
        office.resume_task(done)


# API account problems ---------------------------------------------------------------------------
class _Boom(Exception):
    def __init__(self, text: str, status: int):
        super().__init__(text)
        self.status_code = status


def test_api_problems_are_named_in_plain_words():
    credit = api_problem(_Boom("Error code: 400 - Your credit balance is too low to access the "
                               "Anthropic API.", 400))
    assert credit and credit[0] == "api_credit" and "Console" in credit[1]
    assert api_problem(_Boom("invalid x-api-key", 401))[0] == "api_auth"
    assert api_problem(_Boom("slow down", 429))[0] == "api_rate"
    assert api_problem(_Boom("upstream", 529))[0] == "api_busy"
    assert api_problem(ValueError("a bug")) is None


async def test_out_of_credit_pauses_work_once_and_resumes_together(make_office):
    office, llm = make_office()
    out_of_credit = _Boom("Your credit balance is too low to access the Anthropic API.", 400)

    async def refuse(params):
        raise out_of_credit

    llm.script("er_lead", refuse)
    llm.script("screen_lead", refuse)
    a = office.assign("er_lead", "First.")
    b = office.assign("screen_lead", "Second.")
    await office.idle()
    for t in (a, b):
        task = office.store.task(t)
        assert task["status"] == "paused" and task["status_reason"].startswith("api_credit")
    incidents = office.store.incidents()
    assert [i["kind"] for i in incidents] == ["api_credit"]   # one incident, not one per task
    assert "out of credit" in incidents[0]["detail"]
    llm.script("er_lead", text_turn("Back at it."))
    llm.script("screen_lead", text_turn("Back at it too."))
    assert sorted(office.resume_from_incident(incidents[0]["id"])) == [a, b]
    await office.idle()
    assert {office.store.task(a)["status"], office.store.task(b)["status"]} == {"done"}


def test_resume_endpoint(make_office):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, llm = make_office()
    with TestClient(create_app(office_factory=lambda: office)) as c:
        assert c.post("/api/incidents/99/resume").status_code == 404
        t = office.store.create_task(assignee="er_lead", assigned_by="captain", kind="assignment",
                                     title="Stuck", body="x")
        office.store.set_task_status(t, "paused", reason="turn_cap: limit")
        inc = office.raise_incident("er_lead", t, "turn_cap", "limit")
        llm.script("er_lead", text_turn("Finished."))
        r = c.post(f"/api/incidents/{inc}/resume")
        assert r.status_code == 200 and r.json() == {"resumed": [t]}
        assert c.post(f"/api/incidents/{inc}/resume").status_code == 404   # already closed
