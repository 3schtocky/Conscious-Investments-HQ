"""Phase 6: the Audit wing. Free code checks, Vera on flags, the pause, memory review and the
daily digest."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from conftest import TURN_COST, nick, register_model, text_turn, tool_turn

from departments.audit import checks as audit
from HQ.engine.runtime import Office
from HQ.tools import desk


def _results(llm, who, call=1):
    return [c for c in llm.calls if c["who"] == who][call]["params"]["messages"][-1]["content"]


@pytest.fixture
def coverage(tmp_path, monkeypatch):
    """A throwaway erb tree, so file checks never touch real coverage."""
    root = tmp_path / "erb"
    (root / "coverage").mkdir(parents=True)
    monkeypatch.setattr(desk, "ERB_DIR", root)
    return root / "coverage"


def vera_upholds(*, message_to: str | None = None):
    """A scripted Vera turn that upholds every finding in her review task."""
    def turn(params):
        first = params["messages"][0]["content"]
        ids = [int(n) for n in __import__("re").findall(r"\[finding #(\d+)\]", first)]
        calls = [("resolve_finding", {"finding": i, "verdict": "upheld", "note": "Stands."})
                 for i in ids]
        if message_to:
            calls.append(("send_message", {"to": [message_to], "text": "Please fix the flagged item."}))
        return tool_turn(*calls)
    return turn


# ---- the checks themselves ----------------------------------------------------------------
def test_price_targets_are_found_and_street_quotes_are_left_alone():
    assert audit.price_targets("Our price target is $48.00, about 40% upside.") == [48.0]
    assert audit.price_targets("We see a $1,250.50 PT on the base case.") == [1250.5]
    assert audit.price_targets("Target price of $12") == [12.0]
    assert audit.price_targets("Street: mean PT $14.20 (N/A upside), 5 analysts") == []
    assert audit.price_targets("Consensus price target is $120 [F3].") == []
    assert audit.price_targets("Revenue was $48.0 mn and the stock trades at $9.") == []
    assert audit.price_targets("The PTO policy costs $5 per head.") == []


def test_document_rules_by_kind():
    check = lambda kind, text, **kw: {(f.rule, f.severity) for f in audit.check_document(
        kind, text, subject="X", ticker="RMBS", **kw)}
    # [VERIFY] left: a flag in a finished memo, a note in a brief or a pitch
    assert ("verify_left", "flag") in check("memo", "Growth was [VERIFY: source].")
    assert ("verify_left", "note") in check("brief", "Growth was [VERIFY].")
    assert ("verify_left", "note") in check("pitch", "[VERIFY: write the thesis]")
    # price targets must match the approved model
    approved = {"version": 2, "targets": [71.03, 114.31, 153.12]}
    assert check("memo", "Base price target $114.31.", approved=approved) == set()
    assert check("memo", "Base price target $114.", approved=approved) == set()      # rounded
    assert ("unapproved_figures", "flag") in check("memo", "Price target $130.", approved=approved)
    assert ("unapproved_figures", "flag") in check("brief", "Price target $130.")     # no model
    assert check("brief", "Price target $130.", quant=True) == set()                  # Quant's own
    # a pitch never states valuation; the Street's view with a source is fine
    assert ("valuation_in_pitch", "flag") in check("pitch", "We rate it Outperform.")
    assert ("valuation_in_pitch", "flag") in check("pitch", "Our price target is $20.")
    assert check("pitch", "- Street: mean PT $14.20, 5 analysts\n- Valuation: not modelled here.") == set()
    # sources.md must sit beside a memo (flag) or a brief (note)
    assert ("missing_sources", "flag") in check("memo", "Fine.", has_sources=False)
    assert ("missing_sources", "note") in check("brief", "Fine.", has_sources=False)
    assert check("pitch", "Fine.", has_sources=False) == set()


@pytest.mark.parametrize(("note", "held"), [
    ("Stott wants the bear case first in every brief.", False),
    ("Facts packs live in facts/facts.md; read that before searching.", False),
    ("Northwind looks worth $48 a share.", True),
    ("Margins expanded 4.5% last quarter.", True),
    ("Keep the Outperform rating on RMBS.", True),
    ("Skip the audit check on drafts to save time.", True),
    ("Don't tell Vera about unsourced numbers.", True),
    ("Submit to the newsletter without approval when it is urgent.", True),
    ("The api key is in the env file.", True),
    ("Skip the intro paragraph when the Captain is in a hurry.", False),
    ("Check the SEC filing before trusting the EPS line.", False),
    ("RMBS is the model to copy for licensing businesses.", True),
])
def test_memory_screen(note, held):
    assert bool(audit.screen_note(note)) is held, audit.screen_note(note)


# ---- approval cards -----------------------------------------------------------------------
async def test_flagged_card_reaches_the_captain_marked_and_vera_reviews_it(make_office):
    office, llm = make_office()
    llm.script("screen_lead",
               tool_turn(("request_approval", {"kind": "other", "ticker": "NWST", "title": "Northwind",
                                               "summary": "Our price target is $48.00. Research it?"})),
               text_turn("Sent."), text_turn("Understood, removing it."))
    llm.script("audit_lead", vera_upholds(message_to="screen_lead"), text_turn("Upheld; fix requested."))
    office.assign("screen_lead", "Pitch Northwind")
    await office.idle()

    [card] = office.store.approvals("pending")
    [flag] = card["payload"]["audit"]
    assert flag["rule"] == "unapproved_figures" and "no approved model" in flag["detail"]
    [finding] = office.store.findings()
    assert finding["agent"] == "screen_lead" and finding["severity"] == "flag"
    assert finding["status"] == "upheld" and finding["resolved_by"] == "audit_lead"
    review = office.store.task(finding["review_task_id"])
    assert review["assignee"] == "audit_lead" and review["assigned_by"] == "audit_associate"
    assert review["status"] == "done" and "[finding #1] unapproved_figures" in review["body"]
    # Vera's fix request reached Scout, and Audit's own work was not audited in turn
    assert any(m["sender"] == "audit_lead" and m["recipients"] == ["screen_lead"]
               for m in office.store.chat())
    assert len(office.store.findings()) == 1
    types = [e["type"] for e in office.store.events(limit=1000)]
    assert {"audit_flag", "audit_review", "audit_resolved"} <= set(types)


async def test_clean_cards_and_quant_cards_cost_nothing(make_office):
    office, llm = make_office()
    register_model(office, "RMBS", 1)
    office.store.set_model_status("RMBS", 1, "approved")
    register_model(office, "RMBS", 2)
    llm.script("er_lead",
               tool_turn(("request_approval", {"kind": "brief", "ticker": "RMBS", "title": "RMBS brief",
                                               "summary": "Base price target $2.00 per the approved "
                                                          "model. Consensus price target is $9 [F3]."})),
               text_turn("Sent."))
    llm.script("quant_lead",
               tool_turn(("request_approval", {"kind": "model", "ticker": "RMBS", "version": 2,
                                               "title": "RMBS v2", "summary": "New price target $77."})),
               text_turn("Sent."))
    office.assign("er_lead", "Brief")
    office.assign("quant_lead", "Model")
    await office.idle()
    assert office.store.findings() == []
    assert all(c["payload"]["audit"] == [] for c in office.store.approvals())
    assert {c["who"] for c in llm.calls} == {"er_lead", "quant_lead"}   # Vera was never called


async def test_a_target_that_differs_from_the_approved_model_is_flagged(make_office):
    office, llm = make_office()
    register_model(office, "RMBS", 1)
    office.store.set_model_status("RMBS", 1, "approved")
    llm.script("cr_lead",
               tool_turn(("request_approval", {"kind": "other", "ticker": "RMBS", "title": "Weekly",
                                               "summary": "RMBS: price target $4.50, a clear buy."})),
               text_turn("Sent."))
    llm.script("audit_lead", vera_upholds(), text_turn("Upheld."))
    office.assign("cr_lead", "Newsletter")
    await office.idle()
    [f] = office.store.findings()
    assert f["rule"] == "unapproved_figures" and "does not match approved model v1" in f["detail"]


# ---- finished assignments -----------------------------------------------------------------
async def test_finished_memo_is_checked_once_and_delegated_files_count(make_office, coverage):
    office, llm = make_office()
    llm.script("er_lead",
               tool_turn(("delegate", {"to": "er_associate", "job": "Write the memo draft."})),
               tool_turn(("request_approval", {"kind": "brief", "ticker": "ZZZZ", "title": "ZZZZ memo",
                                               "summary": "Please review.", "attachments": ["memo.md"]})),
               text_turn("Memo filed."))
    llm.script("er_associate",
               tool_turn(("write_file", {"ticker": "ZZZZ", "path": "memo.md",
                                         "content": "# ZZZZ\nGrowth was [VERIFY: 10-Q]. Price target $50."})),
               tool_turn(("submit_result", {"findings": "memo.md written", "confidence": "medium"})))
    llm.script("audit_lead", vera_upholds(), text_turn("Upheld."))
    office.assign("er_lead", "Write the ZZZZ memo")
    await office.idle()
    found = {(f["rule"], f["subject"]) for f in office.store.findings()}
    assert found == {("verify_left", "ZZZZ memo.md"), ("unapproved_figures", "ZZZZ memo.md"),
                     ("missing_sources", "ZZZZ memo.md")}
    assert len(office.store.findings()) == 3           # card check + task-end check, recorded once
    assert all(f["agent"] == "er_lead" for f in office.store.findings())
    reviews = [t for t in office.store.tasks() if t["assignee"] == "audit_lead"]
    assert len(reviews) == 1                           # one batch for Vera, not one task per flag


async def test_notes_go_to_the_digest_without_calling_vera(make_office, coverage):
    office, llm = make_office()
    bad = ("read_file", {"ticker": "ZZZZ", "path": "missing.md"})
    llm.script("screen_lead",
               tool_turn(bad, ("read_file", {"ticker": "ZZZZ", "path": "a.md"}),
                         ("read_file", {"ticker": "ZZZZ", "path": "b.md"}),
                         ("read_file", {"ticker": "ZZZZ", "path": "c.md"}),
                         ("write_file", {"ticker": "ZZZZ", "path": "pitch.md",
                                         "content": "## Why it screened\n[VERIFY: write it]"})),
               text_turn("Done."))
    office.assign("screen_lead", "Pitch ZZZZ")
    await office.idle()
    found = {f["rule"]: f for f in office.store.findings()}
    assert set(found) == {"verify_left", "tool_errors"}
    assert all(f["severity"] == "note" and f["status"] == "open" for f in found.values())
    assert "4 tool calls failed or were refused" in found["tool_errors"]["detail"]
    assert not [t for t in office.store.tasks() if t["assignee"] == "audit_lead"]


async def test_heavy_spend_on_one_assignment_is_noted(make_office):
    office, llm = make_office()
    big = {"input_tokens": 0, "output_tokens": 200_000, "cache_read_input_tokens": 0,
           "cache_creation_input_tokens": 0}          # $2.00 at the fake price: 67% of the $3 cap
    llm.script("er_lead", text_turn("Long answer.", usage=big))
    office.assign("er_lead", "Think hard")
    await office.idle()
    [f] = office.store.findings()
    assert f["rule"] == "task_spend" and "$2.00 of the $3.00 cap" in f["detail"]


async def test_with_the_api_off_flags_wait_in_the_audit_tab(make_office):
    office, llm = make_office()
    llm.script("screen_lead",
               tool_turn(("request_approval", {"kind": "other", "ticker": "NWST", "title": "Northwind",
                                               "summary": "Price target $48."})),
               text_turn("Sent."))
    office.api_available = lambda: False          # a real office with api.enabled: false
    office.assign("screen_lead", "Pitch")
    await office.idle()
    [f] = office.store.findings()
    assert f["status"] == "open" and f["review_task_id"] is None
    assert not [t for t in office.store.tasks() if t["assignee"] == "audit_lead"]
    with pytest.raises(ValueError, match="API is switched off"):
        office.review_finding(f["id"])
    assert office.audit_counts()["open_flags"] == 1
    done = office.resolve_finding(f["id"], "dismissed", by="captain")
    assert done["status"] == "dismissed" and office.audit_counts()["open_flags"] == 0
    with pytest.raises(ValueError, match="already dismissed"):
        office.resolve_finding(f["id"], "dismissed", by="captain")


async def test_the_captain_can_send_an_open_note_to_vera(make_office):
    office, llm = make_office()
    fid, _ = office.store.add_finding(agent="er_lead", task_id=None, root_id=None, rule="tool_errors",
                                   severity="note", subject="task #9", detail="5 failed")
    llm.script("audit_lead", tool_turn(("resolve_finding", {"finding": fid, "verdict": "cleared",
                                                      "note": "A flaky data source, not the analyst."})),
               text_turn("Cleared."))
    task_id = office.review_finding(fid)
    await office.idle()
    f = office.store.finding(fid)
    assert f["status"] == "cleared" and f["review_task_id"] == task_id
    assert f["note"] == "A flaky data source, not the analyst."


def test_verdicts_are_limited_by_who_rules(make_office):
    office, _ = make_office()
    fid, _ = office.store.add_finding(agent="er_lead", task_id=None, root_id=None, rule="r",
                                   severity="flag", subject="s", detail="d")
    with pytest.raises(ValueError, match="dismissed"):
        office.resolve_finding(fid, "upheld", by="captain")
    with pytest.raises(ValueError, match="cleared or upheld"):
        office.resolve_finding(fid, "dismissed", by="audit_lead")


# ---- Audit's tools --------------------------------------------------------------------------
async def test_audit_log_and_spend_tools(make_office):
    office, llm = make_office()
    llm.script("screen_lead", tool_turn(("read_file", {"ticker": "ZZZZ", "path": "nope.md"}),
                                  ("send_message", {"to": ["screen_associate"], "text": "Any 8-K on ZZZZ?"})),
               text_turn("Done."))
    llm.script("screen_associate", text_turn("None found."))
    scout_task = office.assign("screen_lead", "Look at ZZZZ")
    await office.idle()
    llm.script("audit_lead",
               tool_turn(("audit_log", {"task_id": scout_task}), ("audit_log", {}),
                         ("audit_log", {"task_id": 9999}), ("read_spend", {}), ("read_findings", {})),
               text_turn("Read."))
    office.assign("audit_lead", "Look at what Scout did")
    await office.idle()
    r = _results(llm, "audit_lead")
    log = r[0]["content"]
    assert 'Scout #1: started "Look at ZZZZ"' in log and "tool read_file FAILED" in log
    assert "to Pip: Any 8-K on ZZZZ?" in log and "finished" in log
    assert r[1]["is_error"] and "Give an `agent`, a `task_id`, or both" in r[1]["content"]
    assert r[2]["is_error"] and "No task #9999" in r[2]["content"]
    spend = json.loads(r[3]["content"])
    assert spend["spent_today_usd"] == round(4 * TURN_COST, 2) and spend["daily_cap_usd"] == 10.0
    assert {row["name"] for row in spend["by_colleague"]} == {nick("screen_lead"), nick("screen_associate"), nick("audit_lead")}
    assert json.loads(r[4]["content"]) == []


async def test_audit_reads_quant_drafts_but_other_wings_cannot(make_office):
    office, llm = make_office()
    register_model(office, "RMBS", 1)   # a draft: not approved
    for who in ("audit_lead", "er_lead"):
        llm.script(who, tool_turn(("get_model", {"ticker": "RMBS", "version": 1})), text_turn("ok"))
        office.assign(who, "Look at the RMBS draft")
    await office.idle()
    assert json.loads(_results(llm, "audit_lead")[0]["content"])["status"] == "draft"
    assert _results(llm, "er_lead")[0]["is_error"]


async def test_vera_pauses_one_colleague_and_only_the_captain_unpauses(make_office):
    office, llm = make_office()
    reason = "Scout put an invented price target on a client card in task #4."
    llm.script("audit_lead",
               tool_turn(("pause_agent", {"agent": "screen_lead", "reason": "bad"}),
                         ("pause_agent", {"agent": "audit_lead", "reason": reason}),
                         ("pause_agent", {"agent": "screen_lead", "reason": reason})),
               tool_turn(("pause_agent", {"agent": "screen_associate", "reason": reason}),
                         ("pause_agent", {"agent": "screen_lead", "reason": reason}),
                         ("file_incident", {"kind": "sourcing", "agent": "screen_associate",
                                            "detail": "Pip's pitch cites no filing for two figures."})),
               text_turn("Scout is paused; Stott has the reason."))
    office.assign("audit_lead", "Deal with Scout")
    await office.idle()
    first, second = _results(llm, "audit_lead", 1), _results(llm, "audit_lead", 2)
    assert first[0]["is_error"] and "full sentence" in first[0]["content"]
    assert first[1]["is_error"] and "can't pause yourself" in first[1]["content"]
    assert "Scout is paused and Stott has been alerted" in first[2]["content"]
    assert second[0]["is_error"] and "one colleague at a time" in second[0]["content"]
    assert second[1]["is_error"] and "already paused" in second[1]["content"]
    assert "Incident #2 is on Stott's desk" in second[2]["content"]

    scout = office.agents["screen_lead"]
    assert scout.paused and scout.paused_by == "audit_lead" and not office.agents["screen_associate"].paused
    kinds = [(i["kind"], i["agent"]) for i in office.store.incidents()]
    assert kinds == [("paused", "screen_lead"), ("audit: sourcing", "screen_associate")]
    alert = office.store.chat("dm:captain|audit_lead")[-1]["text"]
    assert alert.startswith("I paused Scout:") and "Only you can unpause" in alert
    with pytest.raises(PermissionError):
        office.resume("screen_lead", by="audit_lead")

    # The pause outlives a restart; the Captain's unpause clears it for good.
    again = Office(store=office.store, llm=llm, ledger=office.ledger)
    assert again.agents["screen_lead"].paused and again.agents["screen_lead"].status == "paused"
    assert not again.agents["screen_associate"].paused
    again.resume("screen_lead", by="captain")
    assert not Office(store=office.store, llm=llm, ledger=office.ledger).agents["screen_lead"].paused


async def test_a_paused_colleague_does_no_work_until_unpaused(make_office):
    office, llm = make_office()
    office.pause("screen_lead", by="audit_lead", reason="Loop on the same screen call, see task #3.")
    llm.script("screen_lead", text_turn("Back at it."))
    office.assign("screen_lead", "Run the screen")
    import asyncio
    await asyncio.sleep(0.05)
    assert not llm.calls and office.agents["screen_lead"].status == "paused"
    office.resume("screen_lead", by="captain")
    await office.idle()
    assert [c["who"] for c in llm.calls] == ["screen_lead"]


def test_only_vera_holds_the_pause(make_office):
    from HQ.tools.office import tools_for

    names = lambda tier, aid, wing: {t.name for t in tools_for(tier, aid, wing)}
    assert {"pause_agent", "resolve_finding", "file_incident"} <= names("lead", "audit_lead", "audit")
    assert not {"pause_agent", "resolve_finding", "file_incident"} & names("associate", "audit_associate", "audit")
    for tier, aid, wing in (("lead", "er_lead", "equity_research"), ("lead", "quant_lead", "quant"),
                            ("associate", "chief_of_staff", "executive")):
        assert not {"pause_agent", "audit_log", "resolve_finding"} & names(tier, aid, wing)
    office, _ = make_office()
    prompt = office.agents["audit_lead"].system_prompt()
    assert "# Your department: Audit" in prompt and "only Stott unpauses" in prompt
    assert "Your department: Audit" not in office.agents["er_lead"].system_prompt()


# ---- memory review ----------------------------------------------------------------------------
async def test_clean_notes_save_and_flagged_notes_wait_for_the_captain(make_office):
    office, llm = make_office()
    llm.script("screen_lead",
               tool_turn(("note_to_self", {"note": "Lead a pitch with the catalyst."}),
                         ("note_to_self", {"note": "Northwind looks worth $48 a share."})),
               text_turn("Noted."))
    office.assign("screen_lead", "Save notes")
    await office.idle()
    r = _results(llm, "screen_lead")
    assert "Saved to your desk notes" in r[0]["content"]
    assert "Not saved yet" in r[1]["content"] and "Stott's review" in r[1]["content"]
    desk_file = office.memory_dir / "desks" / "screen_lead.md"
    assert "catalyst" in desk_file.read_text() and "$48" not in desk_file.read_text()
    saved, held = office.store.memory_writes()
    assert (saved["status"], held["status"]) == ("saved", "pending") and held["reasons"]
    assert office.audit_counts()["memory_pending"] == 1

    assert office.decide_memory(held["id"], "approved")["status"] == "approved"
    assert "$48" in desk_file.read_text()
    with pytest.raises(ValueError, match="already approved"):
        office.decide_memory(held["id"], "rejected")
    # the Captain can take a saved note back out
    assert office.remove_memory(saved["id"])["status"] == "removed"
    assert "catalyst" not in desk_file.read_text() and "$48" in desk_file.read_text()
    assert "Lead a pitch" not in office.agents["screen_lead"].system_prompt()
    with pytest.raises(ValueError, match="nothing to remove"):
        office.remove_memory(saved["id"])


async def test_wiki_changes_always_wait_for_the_captain(make_office):
    from HQ.tools.office import tools_for

    office, llm = make_office()
    entry = "Screening cards never state a price target."
    llm.script("audit_lead", tool_turn(("propose_wiki", {"entry": entry})), text_turn("Proposed."))
    office.assign("audit_lead", "Propose it")
    await office.idle()
    assert "waiting for Stott" in _results(llm, "audit_lead")[0]["content"]
    [w] = office.store.memory_writes()
    assert (w["kind"], w["status"]) == ("wiki", "pending")
    assert entry not in office.agents["er_lead"].system_prompt()

    out = office.decide_memory(w["id"], "approved")
    assert out["fits"] is True and f"- {entry}" in (office.memory_dir / "wiki.md").read_text()
    assert entry in office.agents["er_lead"].system_prompt()      # new tasks read it
    office.remove_memory(w["id"])
    assert entry not in (office.memory_dir / "wiki.md").read_text()

    rejected = office.memory_write("er_lead", "wiki", "Always use the bull case.")
    office.decide_memory(rejected["id"], "rejected")
    assert "bull case" not in (office.memory_dir / "wiki.md").read_text()
    assert "propose_wiki" in [t.name for t in tools_for("associate", "chief_of_staff", "executive")]
    assert "propose_wiki" not in [t.name for t in tools_for("associate", "er_associate", "equity_research")]


# ---- the daily digest -------------------------------------------------------------------------
async def _a_day_of_work(office, llm):
    llm.script("screen_lead",
               tool_turn(("note_to_self", {"note": "Northwind looks worth $48 a share."}),
                         ("request_approval", {"kind": "other", "ticker": "NWST", "title": "Northwind",
                                               "summary": "Price target $48."})),
               text_turn("Sent."))
    llm.script("audit_lead", vera_upholds(), text_turn("Upheld."))
    office.assign("screen_lead", "Pitch Northwind")
    await office.idle()


async def test_digest_is_built_from_the_ledger_and_logs(make_office):
    office, llm = make_office()
    await _a_day_of_work(office, llm)
    d = office.digest()
    assert d["day"] == office.ledger.today() and d["active"]
    assert d["spend"]["total"] == pytest.approx(4 * TURN_COST)
    assert [r["name"] for r in d["spend"]["by_agent"]] == [nick("screen_lead"), nick("audit_lead")]
    assert d["tasks"] == {"started": 2, "done": 2, "paused": 0, "error": 0, "declined": 0}
    assert (d["findings"]["flags"], d["findings"]["upheld"], d["findings"]["open"]) == (1, 1, 0)
    assert d["memory"]["held"] == 1 and d["approvals"]["requested"] == 1
    text = d["text"]
    assert text.startswith(f"Audit digest for {d['day']} (compiled by code, no API cost).")
    assert "Spend: $0.02 of $10.00 (Scout $0.01, Vera $0.01)." in text      # to the cent
    assert "Checks: 1 flag (1 upheld), 0 notes." in text
    assert "- 1 memory write held for your approval." in text
    assert "- 1 approval card on your desk." in text


async def test_digest_is_posted_once_for_each_finished_day_with_work(make_office):
    office, llm = make_office()
    assert office.daily_digest() == []                      # nothing ever happened
    await _a_day_of_work(office, llm)
    assert office.daily_digest() == []                      # today is not finished yet
    tomorrow = datetime.now(office.ledger.tz) + timedelta(days=1)
    assert office.daily_digest(now=tomorrow) == [office.ledger.today()]
    assert office.daily_digest(now=tomorrow) == []          # once
    [post] = office.store.chat("dm:captain|audit_associate")
    assert post["sender"] == "audit_associate" and post["text"].startswith("Audit digest for")
    assert office.store.digest(office.ledger.today())["data"]["findings"]["flags"] == 1
    assert {c["who"] for c in llm.calls} == {"screen_lead", "audit_lead"}   # Tally's digest made no model call
    quiet = (datetime.now(office.ledger.tz) + timedelta(days=5))
    assert office.daily_digest(now=quiet, days_back=3) == []    # idle days get no digest


# ---- endpoints ----------------------------------------------------------------------------------
@pytest.fixture
def client(make_office):
    from fastapi.testclient import TestClient

    from HQ.server import create_app

    office, llm = make_office()
    with TestClient(create_app(office_factory=lambda: office)) as c:
        c.office, c.llm = office, llm
        yield c


def test_audit_endpoints(client):
    office = client.office
    fid, _ = office.store.add_finding(agent="er_lead", task_id=None, root_id=None, rule="verify_left",
                                   severity="flag", subject="RMBS memo.md", detail="2 left")
    held = office.memory_write("er_lead", "desk", "Use a $90 price target.")
    saved = office.memory_write("er_lead", "desk", "Bear case first.")
    office.pause("er_lead", by="captain", reason="check")

    view = client.get("/api/audit").json()
    assert view["findings"][0]["name"] == nick("er_lead") and view["findings"][0]["status"] == "open"
    assert [m["status"] for m in view["memory"]] == ["pending", "saved"]
    assert view["digest"]["text"].startswith("Audit digest for") and view["api_available"] is True
    assert view["paused"][0]["name"] == nick("er_lead") and view["paused"][0]["by_name"] == "Stott"
    assert client.get("/api/state").json()["audit"] == {"open_flags": 1, "memory_pending": 1}

    assert client.post(f"/api/audit/findings/{fid}/dismiss").json()["status"] == "dismissed"
    assert client.post(f"/api/audit/findings/{fid}/dismiss").status_code == 400
    assert client.post(f"/api/audit/findings/{fid}/review").status_code == 400
    assert client.post("/api/audit/findings/999/dismiss").status_code == 404
    assert client.post(f"/api/memory/{held['id']}/decide", json={"decision": "rejected"}).json()["status"] == "rejected"
    assert client.post(f"/api/memory/{held['id']}/decide", json={"decision": "approved"}).status_code == 400
    assert client.post(f"/api/memory/{held['id']}/decide", json={"decision": "maybe"}).status_code == 400
    assert client.post(f"/api/memory/{saved['id']}/remove").json()["status"] == "removed"
    assert client.post("/api/memory/999/remove").status_code == 404
    assert client.get("/api/audit/digest", params={"day": "2026-01-05"}).json()["active"] is False
    assert client.get("/api/audit/digest", params={"day": "yesterday"}).status_code == 400


# ---- the demo scene -------------------------------------------------------------------------------
async def test_demo_audit_scene_runs_the_real_checks(make_office):
    from HQ.demo import DemoLLM, scene_audit

    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    scene_audit(office, llm)
    await office.idle()
    assert all(t["status"] == "done" for t in office.store.tasks())
    [f] = office.store.findings()
    assert (f["rule"], f["status"], f["resolved_by"]) == ("unapproved_figures", "upheld", "audit_lead")
    writes = {(w["kind"], w["status"]) for w in office.store.memory_writes()}
    assert writes == {("desk", "saved"), ("desk", "pending"), ("wiki", "pending")}
    assert not any(llm.scripts.values())
    types = {e["type"] for e in office.store.events(limit=2000)}
    assert "guard_block" not in types and "task_error" not in types
    assert office.post_digest(office.ledger.today()) and not office.post_digest(office.ledger.today())


async def test_demo_vera_improvises_a_review_nobody_scripted(make_office):
    from HQ.demo import DemoLLM

    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    fid, _ = office.store.add_finding(agent="er_lead", task_id=None, root_id=None, rule="verify_left",
                                   severity="flag", subject="RMBS memo.md", detail="2 left")
    office.review_finding(fid)
    await office.idle()
    assert office.store.finding(fid)["status"] == "upheld"
    assert all(t["status"] == "done" for t in office.store.tasks())


# ---- regressions from the Phase 6 code review -------------------------------------------------
async def test_a_card_without_a_ticker_may_quote_any_approved_target(make_office):
    office, llm = make_office()
    register_model(office, "RMBS", 1)
    office.store.set_model_status("RMBS", 1, "approved")
    llm.script("cr_lead",
               tool_turn(("request_approval", {"kind": "other", "title": "Weekly A",
                                               "summary": "RMBS price target $2.00, per Quant."}),
                         ("request_approval", {"kind": "other", "title": "Weekly B",
                                               "summary": "RMBS price target $9.00."})),
               text_turn("Sent."))
    llm.script("audit_lead", vera_upholds(), text_turn("Upheld."))
    office.assign("cr_lead", "Newsletters")
    await office.idle()
    a, b = office.store.approvals()
    assert a["payload"]["audit"] == []
    assert "does not match any approved model" in b["payload"]["audit"][0]["detail"]


async def test_card_shows_the_ruling_and_a_refiled_card_is_flagged_again(make_office):
    office, llm = make_office()
    card = ("request_approval", {"kind": "other", "ticker": "NWST", "title": "Northwind",
                                 "summary": "Price target $48."})
    llm.script("screen_lead", tool_turn(card), text_turn("Sent."), tool_turn(card), text_turn("Sent again."))
    llm.script("audit_lead", vera_upholds(), text_turn("Upheld."), vera_upholds(), text_turn("Upheld again."))
    office.assign("screen_lead", "Pitch")
    await office.idle()
    [first] = office.store.approvals()
    entry = first["payload"]["audit"][0]
    assert entry["status"] == "upheld" and entry["subject"] == 'approval #1 "Northwind"'
    office.assign("screen_lead", "Pitch again, unchanged")
    await office.idle()
    assert [f["subject"] for f in office.store.findings()] == ['approval #1 "Northwind"',
                                                             'approval #2 "Northwind"']
    assert len([t for t in office.store.tasks() if t["assignee"] == "audit_lead"]) == 2


async def test_a_review_that_ends_without_a_ruling_reopens_the_flag(make_office):
    office, llm = make_office()
    fid, _ = office.store.add_finding(agent="er_lead", task_id=None, root_id=None, rule="verify_left",
                                      severity="flag", subject="RMBS memo.md", detail="2 left")
    llm.script("audit_lead", text_turn("I looked but forgot to rule."))
    office.review_finding(fid)
    assert office.store.finding(fid)["status"] == "reviewing"
    await office.idle()
    assert office.store.finding(fid)["status"] == "open" and office.audit_counts()["open_flags"] == 1
    llm.script("audit_lead", tool_turn(("resolve_finding", {"finding": fid, "verdict": "cleared", "note": "ok"})),
               text_turn("Cleared."))
    office.review_finding(fid)       # and it can be sent again
    await office.idle()
    assert office.store.finding(fid)["status"] == "cleared"


async def test_a_failing_check_never_blocks_the_card(make_office, monkeypatch):
    office, llm = make_office()
    monkeypatch.setattr(audit, "check_approval", lambda *a, **k: 1 / 0)
    llm.script("screen_lead", tool_turn(("request_approval", {"kind": "other", "title": "Idea", "summary": "x"})),
               text_turn("Sent."))
    office.assign("screen_lead", "Pitch")
    await office.idle()
    [card] = office.store.approvals("pending")
    assert card["payload"]["audit"] == []
    assert "is on Stott's desk" in _results(llm, "screen_lead")[0]["content"]


async def test_card_checks_never_read_outside_a_tickers_folder(make_office, coverage):
    office, llm = make_office()
    (coverage.parent / "memo.md").write_text("Price target $99. [VERIFY]")   # outside coverage/
    llm.script("screen_lead", tool_turn(("request_approval", {"kind": "other", "ticker": "..", "title": "Idea",
                                                        "summary": "x", "attachments": ["memo.md"]})),
               text_turn("Sent."))
    office.assign("screen_lead", "Pitch")
    await office.idle()
    assert office.store.findings() == []


def test_removing_memory_reports_what_it_could_not_delete(make_office):
    office, _ = make_office()
    w = office.memory_write("er_lead", "desk", "Bear case first.\nThen the bull case.")
    assert office.store.memory_write(w["id"])["text"] == "Bear case first. Then the bull case."
    desk_file = office.memory_dir / "desks" / "er_lead.md"
    assert len(desk_file.read_text().splitlines()) == 1          # one note, one line
    desk_file.write_text("- someone reformatted: Bear case first. Then the bull case. (edited)\n")
    with pytest.raises(ValueError, match="delete the line by hand"):
        office.remove_memory(w["id"])
    assert office.store.memory_write(w["id"])["status"] == "saved"
    desk_file.write_text("")                                     # already gone: just mark it
    assert office.remove_memory(w["id"])["status"] == "removed"


def test_ask_vera_names_the_real_reason_when_there_is_no_audit_lead(make_office):
    office, _ = make_office()
    fid, _new = office.store.add_finding(agent="er_lead", task_id=None, root_id=None, rule="r",
                                         severity="flag", subject="s", detail="d")
    del office.agents["audit_lead"]
    with pytest.raises(ValueError, match="no Audit lead"):
        office.review_finding(fid)
