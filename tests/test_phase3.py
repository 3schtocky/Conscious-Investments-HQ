"""Phase 3: the Captain's channel (tone filter, routing, DMs) and approvals."""

from __future__ import annotations

import asyncio

import pytest
from conftest import text_turn, tool_turn

from hq.engine.llm import TurnResult
from hq.engine.tone import RuleRewriter, check, key_facts


def _drain(q) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# tone check ---------------------------------------------------------------------------------
def test_check_flags_dropped_tickers_numbers_dates_and_negations():
    original = "Don't touch NVDA. Trim RMBS by 10% before Friday and use $3.2bn revenue."
    bad = "Please take a look at the semis position when you can."
    result = check(original, bad)
    assert not result.ok
    assert set(result.missing) == {"tickers", "numbers", "dates", "negations"}
    assert "NVDA" in result.missing["tickers"] and "10%" in result.missing["numbers"]
    good = ("Hi team, thanks for the focus. Please don't touch NVDA. Trim RMBS by 10% before "
            "Friday and use $3.2bn revenue. Thank you.")
    assert check(original, good).ok


def test_check_flags_numbers_the_rewrite_invented():
    result = check("Update the model.", "Update the model with a 12% discount rate.")
    assert not result.ok and result.added_numbers == ["12%"]


def test_key_facts_ignores_ordinary_capitals_and_words_like_margins():
    facts = key_facts("I need this ASAP. OK? Margins at 42.5% for AMD.")
    assert facts["tickers"] == ["AMD"] and facts["dates"] == [] and facts["numbers"] == ["42.5%"]


@pytest.mark.parametrize("msg", [
    "Quill I need you to fix this ASAP!! margins at 42.5% are wrong, use $3.2bn revenue",
    "why haven't you finished the EOSE screen? this is sloppy. need it by Friday",
    "don't publish the newsletter until Vera signs off, and never quote unapproved numbers",
    "Sigma, rerun the DCF with WACC at 9.5% and 2.5% terminal growth by tomorrow",
])
async def test_rule_rewriter_is_warmer_and_keeps_every_fact(msg):
    out = await RuleRewriter().rewrite(msg, "Quill")
    assert out.startswith("Hi Quill,") and check(msg, out).ok, (out, check(msg, out))
    assert " i " not in out and not out.startswith("Hi Quill, T")   # natural casing
    for harsh in ("sloppy", "ASAP", "!!", "why haven't you"):
        assert harsh.lower() not in out.lower()


async def test_llm_rewriter_bills_juno_and_uses_haiku(make_office):
    office, _ = make_office()
    seen = {}

    class ToneLLM:
        async def turn(self, *, params, on_delta=None, on_block=None):
            seen.update(params)
            return TurnResult(content=[{"type": "text", "text": "Hi Quill, please rerun it."}],
                              stop_reason="end_turn", model=params["model"],
                              usage={"input_tokens": 300, "output_tokens": 40,
                                     "cache_read_input_tokens": 0,
                                     "cache_creation_input_tokens": 0})

    office._llm = ToneLLM()
    office.tone_engine = "llm"
    preview = await office.tone_preview("Quill", "rerun it")
    assert preview["rewrite"] == "Hi Quill, please rerun it." and preview["engine"] == "llm"
    from hq.config import model_config
    assert seen["model"] == model_config("associate")[1]["id"]
    assert "messages from Stott, the Captain" in seen["system"]
    assert "transformational leader" in seen["system"] and seen["max_tokens"] == 1024
    rows = office.store.spend_breakdown(office.ledger.today())
    assert rows[0]["agent"] == "chief_of_staff" and rows[0]["cost"] > 0


async def test_tone_preview_falls_back_to_rules_when_budget_is_spent(make_office):
    office, _ = make_office(daily_cap=0.0, audit_reserve=0.0)
    office.tone_engine = "llm"
    preview = await office.tone_preview("office", "Find two gems by Friday")
    assert preview["engine"] == "rules (budget)" and preview["check"]["ok"]


# routing and DMs ----------------------------------------------------------------------------
async def test_office_message_goes_to_juno_who_assigns_a_lead(make_office):
    office, llm = make_office()
    llm.script("Juno",
               tool_turn(("assign_task", {"to": "Sigma", "title": "RMBS DCF",
                                          "brief": "Build the RMBS DCF by Friday."})),
               tool_turn(("report_to_captain", {"text": "Sigma is on the RMBS DCF."})),
               text_turn("Routed."))
    llm.script("Sigma", text_turn("Starting the model."))
    out = office.captain_send("office", "Hi team, please build the RMBS DCF by Friday.",
                              original="build the RMBS DCF by friday")
    await office.idle()
    juno_task = office.store.task(out["task_id"])
    assert juno_task["assignee"] == "chief_of_staff" and juno_task["status"] == "done"
    _, msgs = office.store.conversation(out["task_id"])
    assert "please build the RMBS DCF" in msgs[0]["content"]
    assert "build the RMBS DCF by friday" not in str(msgs)   # agents never see the original
    chat = office.store.chat("captain:office")
    assert chat[0]["original"] == "build the RMBS DCF by friday"
    sigma = next(t for t in office.store.tasks() if t["assignee"] == "quant_lead")
    assert sigma["assigned_by"] == "chief_of_staff" and sigma["kind"] == "assignment"
    assert office.store.chat("dm:chief_of_staff|quant_lead")[0]["text"].startswith("New assignment")


async def test_juno_cannot_assign_to_an_associate(make_office):
    office, llm = make_office()
    llm.script("Juno", tool_turn(("assign_task", {"to": "Delta", "title": "x", "brief": "y"})),
               text_turn("ok"))
    office.captain_send("office", "do something")
    await office.idle()
    call = [c for c in llm.calls if c["who"] == "Juno"][1]
    result = call["params"]["messages"][-1]["content"][0]
    assert result["is_error"] and "Assign work to department leads" in result["content"]


async def test_dm_to_idle_agent_starts_a_task_and_to_busy_agent_lands_in_inbox(make_office):
    office, llm = make_office()
    llm.script("Quill", text_turn("On it."))
    out = office.captain_send("Quill", "Please look at AMD.")
    assert out["delivered"] == "task"
    await office.idle()
    assert office.store.task(out["task_id"])["body"] == "Please look at AMD."

    release = asyncio.Event()

    async def busy(params):
        await release.wait()
        return tool_turn(("send_message", {"to": ["Ledger"], "text": "pulling AMD"}))

    llm.script("Quill", busy, text_turn("Saw Stott's note."))
    llm.script("Ledger", text_turn("ok"))
    office.assign("Quill", "Long job")
    await asyncio.sleep(0.02)
    out = office.captain_send("Quill", "Also check the 10-Q.")
    assert out["delivered"] == "inbox"
    release.set()
    await office.idle()
    last = [c for c in llm.calls if c["who"] == "Quill"][-1]["params"]["messages"][-1]["content"]
    assert any("[Message from Stott (the Captain)]: Also check the 10-Q." in b.get("text", "")
               for b in last)


# approvals ----------------------------------------------------------------------------------
async def test_model_approval_round_trip(make_office):
    office, llm = make_office()
    llm.script("Sigma",
               tool_turn(("request_approval", {"kind": "model", "ticker": "rmbs", "version": 1,
                                               "title": "RMBS model v1", "summary": "Base $X.",
                                               "attachments": ["RMBS_v1.xlsx"]})),
               text_turn("Sent for approval."),
               text_turn("Distributing v1."))
    office.assign("Sigma", "Build RMBS v1")
    await office.idle()
    [card] = office.store.approvals("pending")
    assert card["kind"] == "model" and card["payload"] == {"ticker": "RMBS", "version": 1,
                                                           "attachments": ["RMBS_v1.xlsx"]}
    decided = office.decide(card["id"], "approved", "Good work, ship it.")
    assert decided["status"] == "approved" and decided["note"] == "Good work, ship it."
    await office.idle()
    follow_up = [t for t in office.store.tasks() if t["title"].startswith("Decision on")]
    assert follow_up and "official numbers" in follow_up[0]["body"]
    with pytest.raises(ValueError, match="already approved"):
        office.decide(card["id"], "rejected")


async def test_request_approval_validation(make_office):
    office, llm = make_office()
    llm.script("Sigma",
               tool_turn(("request_approval", {"kind": "model", "title": "x", "summary": "y"}),
                         ("request_approval", {"kind": "vibes", "title": "x", "summary": "y"})),
               text_turn("ok"))
    office.assign("Sigma", "x")
    await office.idle()
    results = [c for c in llm.calls if c["who"] == "Sigma"][1]["params"]["messages"][-1]["content"]
    assert "needs `ticker` and `version`" in results[0]["content"]
    assert "`kind` must be one of" in results[1]["content"]
    assert office.store.approvals() == []


def test_tool_lists_by_role(make_office):
    from hq.tools.office import tools_for

    names = lambda tier, aid: [t.name for t in tools_for(tier, aid)]
    assert names("associate", "chief_of_staff") == ["send_message", "assign_task",
                                                    "report_to_captain", "request_approval"]
    assert names("lead", "er_lead") == ["send_message", "delegate", "report_to_captain",
                                        "request_approval"]
    assert names("associate", "er_associate") == ["send_message", "submit_result"]


# endpoints ----------------------------------------------------------------------------------
@pytest.fixture
def client(make_office):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, llm = make_office()
    office.tone_engine = "rules"
    with TestClient(create_app(office_factory=lambda: office)) as c:
        c.office, c.llm = office, llm
        yield c


def test_preview_and_send_endpoints(client):
    r = client.post("/api/captain/preview", json={"to": "Quill", "text": "fix the AMD model ASAP"})
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "rules" and body["check"]["ok"]
    assert body["rewrite"].startswith("Hi Quill,")
    client.llm.script("Quill", text_turn("On it."))
    sent = client.post("/api/captain/send", json={"to": "Quill", "text": body["rewrite"],
                                                  "original": "fix the AMD model ASAP"})
    assert sent.status_code == 200 and sent.json()["routed_to"] == "er_lead"
    assert client.post("/api/captain/send", json={"to": "nobody", "text": "hi"}).status_code == 404
    assert client.post("/api/captain/send", json={"to": "office", "text": "  "}).status_code == 400


def test_decide_endpoint_errors(client):
    assert client.post("/api/approvals/999/decide", json={"decision": "approved"}).status_code == 404
    aid = client.office.request_approval("quant_lead", kind="other", title="t", summary="s",
                                         payload={}, task_id=None)
    bad = client.post(f"/api/approvals/{aid}/decide", json={"decision": "maybe"})
    assert bad.status_code == 400
    ok = client.post(f"/api/approvals/{aid}/decide", json={"decision": "changes", "note": "more"})
    assert ok.json()["status"] == "changes"
    assert client.get("/api/approvals?status=pending").json() == []


# demo reacts to the Captain -----------------------------------------------------------------
async def test_demo_office_answers_the_captain_for_free(make_office):
    from hq.demo import DemoLLM

    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    office.captain_send("office", "Please run a Monte Carlo on the RMBS valuation.")
    await office.idle()
    tasks = office.store.tasks()
    assert all(t["status"] == "done" for t in tasks), [(t["assignee"], t["status"]) for t in tasks]
    assert {t["assignee"] for t in tasks} >= {"chief_of_staff", "quant_lead", "quant_associate"}
    reports = [c for c in office.store.chat() if "captain" in c["recipients"]]
    assert len(reports) >= 2   # Juno's routing note and Sigma's report


# the master API switch ----------------------------------------------------------------------
async def test_api_switch_off_pauses_tasks_and_tone_falls_back_to_rules(make_office):
    from hq import config

    office, _ = make_office()
    office._llm = None               # the real client, which checks the switch
    office.tone_engine = "llm"
    preview = await office.tone_preview("Quill", "check AMD by Friday")
    assert preview["engine"] == "rules (API off)" and preview["check"]["ok"]
    tid = office.assign("Quill", "anything")
    await office.idle()
    task = office.store.task(tid)
    assert task["status"] == "paused" and task["status_reason"].startswith("api_off")
    assert office.ledger.spent_today() == 0
    assert config.office().get("api", {}).get("enabled") is False


async def test_incident_events_carry_the_incident_id(make_office):
    office, _ = make_office()
    events = office.bus.subscribe()
    office.pause("Quill", by="audit_lead", reason="sourcing check")
    ev = next(e for e in _drain(events) if e.type == "incident")
    [row] = office.store.incidents()
    assert ev.payload["incident_id"] == row["id"] and ev.payload["kind"] == "paused"
    office.resolve_incident(row["id"])
    assert office.store.incidents() == []


def test_titles_skip_the_greeting_paragraph():
    from hq.engine.runtime import _title

    assert _title("Hi team, thanks for the strong work so far.\n\nRun a Monte Carlo on NWST.\n\n"
                  "Thank you.") == "Run a Monte Carlo on NWST."
    assert _title("Hi, quick one") == "Hi, quick one"   # a one-paragraph message keeps its line
    assert _title("Build the RMBS DCF by Friday.") == "Build the RMBS DCF by Friday."


# Regression tests for the Phase 3 code review ---------------------------------------------
async def test_juno_assignments_share_the_captains_task_budget_and_fan_out_limit(make_office):
    office, llm = make_office()
    fan = [("assign_task", {"to": lead, "title": f"t{i}", "brief": f"brief {i}"})
           for i, lead in enumerate(["Quill", "Scout", "Sigma", "Vera", "Harbor", "Quill"])]
    llm.script("Juno", tool_turn(*fan), text_turn("done"))
    for n in ("Quill", "Scout", "Sigma", "Vera", "Harbor"):
        llm.script(n, text_turn("ok"), text_turn("ok"))
    out = office.captain_send("office", "Everyone, please start.")
    await office.idle()
    assigned = [t for t in office.store.tasks() if t["assigned_by"] == "chief_of_staff"]
    assert len(assigned) == 5                                   # the 6th hit the fan-out limit
    assert {t["root_id"] for t in assigned} == {out["task_id"]}   # one shared $ cap


async def test_captain_assign_and_decision_are_announced_live(make_office):
    office, llm = make_office()
    events = office.bus.subscribe()
    llm.script("Quill", text_turn("ok"), text_turn("thanks"))
    office.assign("Quill", "Look at AMD.")
    aid = office.request_approval("er_lead", kind="brief", title="AMD brief", summary="s",
                                  payload={}, task_id=None)
    office.decide(aid, "approved", "go")
    await office.idle()
    msgs = [e for e in _drain(events) if e.type == "captain_message"]
    assert [m.payload["delivered"] for m in msgs] == ["task", "decision"]
