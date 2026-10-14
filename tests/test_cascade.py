"""Model cascade (phase C1): the wing's associate goes first, the lead polishes on a code trigger."""

from __future__ import annotations

import pytest

from hq import cascade, config
from hq.tools.desk import coverage_dir
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


async def test_plain_text_draft_is_not_reported_before_the_cascade_decides(office):
    off, llm, _ = office
    llm.script("quant_associate", tool_turn(("nope", {})), tool_turn(("nope", {})), text_turn("Rough draft."))
    llm.script("quant_lead", text_turn("Polished."))
    tid = await go(off)
    assert tid not in off.reported and polish_of(off, tid)["escalation_reason"] == "gate_fails"


async def test_a_bad_pattern_or_a_removed_kind_cannot_break_assignments(office):
    off, llm, cfg = office
    cfg["kinds"]["model"]["match"] = "(unbalanced"
    llm.script("quant_lead", text_turn("Done."))
    tid = await go(off)
    assert off.store.task(tid)["assignee"] == "quant_lead"
    cfg["kinds"]["model"]["match"] = "model"
    llm.script("quant_associate", tool_turn(("nope", {})), tool_turn(("nope", {})), tool_turn(("submit_result", DONE)))
    llm.script("quant_lead", text_turn("Took it."))
    tid = await go(off, "Build the model for ABC.")
    del cfg["kinds"]["model"]   # edited out of the config after the first pass started
    assert polish_of(off, tid)["assignee"] == "quant_lead"


async def test_lead_reviewed_work_is_counted_apart(office):
    off, llm, cfg = office
    cfg["kinds"]["model"]["lead_review"] = True
    llm.script("quant_associate", tool_turn(("submit_result", DONE)))
    llm.script("quant_lead", text_turn("Reviewed."))
    await go(off)
    s = cascade.summary(off.store)
    assert s["lead_reviewed"] == 1 and s["finished_on_haiku"] == 0 and s["haiku_first"] == 1


def _real_kind(name):
    import yaml
    return yaml.safe_load((config.ROOT / "config" / "office.yaml").read_text())["cascade"]["kinds"][name]


def _real_quant_kind():
    return _real_kind("quant_model")


def test_the_shipped_quant_kind_matches_model_work_only():
    import re
    k = _real_quant_kind()
    assert k["lead"] == "quant_lead" and k["lead_review"] is True
    for yes in ("Build the META model", "Update the RMBS assumptions", "Re-run the valuation for EOSE"):
        assert re.search(k["match"], yes, re.IGNORECASE)
    for no in ("How is Delta doing?", "What model should we use?", "Update me on the META model",
               "Draft a note on the valuation"):
        assert not re.search(k["match"], no, re.IGNORECASE)


async def test_a_failed_formula_check_counts_until_the_same_check_passes(office):
    _, _, cfg = office
    cfg["kinds"]["model"] = {**_real_quant_kind(), "match": "model"}
    task = {"cascade": "model"}
    bad = '{"check": "Formula check FAILED:\\n- Outputs!B4 differs"}'
    good = '{"check": "Formula check PASSED"}'
    errs: list = []
    cascade.record(errs, task, "build_model", bad, False)
    cascade.record(errs, task, "build_model", bad, False)
    assert len(errs) == 2
    cascade.record(errs, task, "build_model", good, False)   # fixed on the third build
    assert errs == []
    cascade.record(errs, task, "read_file", "No file", True)   # not one of the kind's gate tools
    assert errs == []
    cascade.record(errs, task, "run_simulations", "crashed", True)
    assert len(errs) == 1


needs_meta = pytest.mark.skipif(not (coverage_dir("META") / "assumptions.yaml").exists(),
                                reason="needs local META coverage in the submodule")


@needs_meta
async def test_delta_builds_on_real_tools_and_sigma_files_the_approval_card(office):
    off, llm, cfg = office
    cfg["kinds"]["model"] = {**_real_quant_kind(), "match": "model"}
    llm.script("quant_associate", tool_turn(("build_model", {"ticker": "META"})),
               tool_turn(("run_simulations", {"ticker": "META", "runs": 300})),
               tool_turn(("submit_result", {**DONE, "findings": "META model v1 built, check passed."})))
    llm.script("quant_lead",
               tool_turn(("request_approval", {"kind": "model", "ticker": "META", "version": 1,
                                               "title": "META model v1", "summary": "Base target, drivers, risks."})),
               text_turn("Filed."))
    tid = await go(off, "Build the META model from the assumptions.")
    p = polish_of(off, tid)
    assert p["escalation_reason"] == "lead_review" and p["status"] == "done"
    assert "META model v1 built, check passed." in p["body"]
    [m] = off.store.models("META")
    assert m["status"] == "awaiting" and m["summary"]["check"]["ok"]   # on Stott's desk, not yet approved
    [card] = off.store.approvals("pending")
    assert card["kind"] == "model" and card["agent"] == "quant_lead"
    tiers = cascade.by_tier(off.store)["tiers"]
    assert tiers["associate"]["calls"] == 3 and tiers["lead"]["calls"] == 2


def test_the_shipped_research_kind_only_takes_narrow_data_jobs():
    import re
    k = _real_kind("er_facts")
    assert k["lead"] == "er_lead" and k["match_in"] == "title" and not k.get("lead_review")
    for yes in ("Refresh the EOSE facts pack", "Update the peer table for RMBS", "Refresh sources for MU"):
        assert re.search(k["match"], yes, re.IGNORECASE)
    for no in ("Write the RMBS initiation memo", "Research META: facts pack, brief and memo", "What do you think of AMD?",
               "Initiate coverage of RMBS: facts and thesis", "Write reports on peers", "Check the facts on the AMD deal",
               "What are the sources of MU's margin drop?", "Review the sources of the thesis"):
        assert not re.search(k["match"], no, re.IGNORECASE)


def test_a_memo_whose_body_mentions_the_facts_pack_is_not_cascaded(office):
    _, _, cfg = office
    cfg["kinds"]["facts"] = _real_kind("er_facts")
    assert cascade.kind_for("er_lead", "Write the RMBS memo", "Start with the facts pack, then the memo.") is None
    assert cascade.kind_for("er_lead", "Refresh the RMBS facts pack", "Use erb_facts.")[0] == "facts"


@pytest.fixture
def erb(monkeypatch):
    """erb stubbed (no network) for a throwaway ticker; `calls` records each erb command."""
    import shutil

    from hq.tools import desk
    calls, fail = [], {"on": False}

    async def fake_run_erb(*args, timeout=900):
        calls.append(args[0])
        if fail["on"]:
            return 1, "Traceback\nConnectionError: SEC EDGAR unreachable"
        facts = coverage_dir("ZZZZ") / "facts"
        facts.mkdir(parents=True, exist_ok=True)
        (facts / "facts.md").write_text("# Facts\nRevenue FY24: 100 [S1]")
        return 0, "ok"

    monkeypatch.setattr(desk, "run_erb", fake_run_erb)
    shutil.rmtree(coverage_dir("ZZZZ"), ignore_errors=True)   # a killed earlier run may have left it
    yield calls, fail
    shutil.rmtree(coverage_dir("ZZZZ"), ignore_errors=True)


async def test_ledger_refreshes_a_facts_pack_without_waking_quill(office, erb):
    off, llm, cfg = office
    cfg["kinds"]["facts"] = _real_kind("er_facts")
    llm.script("er_associate", tool_turn(("erb_facts", {"ticker": "ZZZZ"})),
               tool_turn(("submit_result", {**DONE, "findings": "ZZZZ facts pack refreshed."})))
    tid = off.assign("er_lead", "Refresh the ZZZZ facts pack")
    await off.idle()
    assert off.store.task(tid)["assignee"] == "er_associate" and polish_of(off, tid) is None
    assert erb[0] == ["facts"] and tid in off.reported
    assert not [c for c in llm.calls if c["who"] == "er_lead"]


async def test_erb_failing_twice_hands_the_job_to_quill_with_the_reason(office, erb):
    off, llm, cfg = office
    cfg["kinds"]["facts"] = _real_kind("er_facts")
    erb[1]["on"] = True
    llm.script("er_associate", tool_turn(("erb_facts", {"ticker": "ZZZZ"})),
               tool_turn(("erb_facts", {"ticker": "ZZZZ"})), text_turn("Could not get the filings."))
    llm.script("er_lead", text_turn("Took it."))
    tid = off.assign("er_lead", "Refresh the ZZZZ facts pack")
    await off.idle()
    p = polish_of(off, tid)
    assert p["assignee"] == "er_lead" and p["escalation_reason"] == "gate_fails"
    assert "SEC EDGAR unreachable" in p["body"] and tid not in off.reported


def test_the_research_associate_can_lint_its_own_work():
    from hq.tools.office import tools_for
    assert "erb_lint" in [t.name for t in tools_for("associate", "er_associate", "equity_research")]


def test_a_mistyped_match_in_fails_closed_to_the_title(office):
    _, _, cfg = office
    cfg["kinds"]["facts"] = {**_real_kind("er_facts"), "match_in": "Titles"}
    assert cascade.kind_for("er_lead", "Write the RMBS memo", "Refresh the facts pack first.") is None


def test_the_shipped_screening_kind_takes_single_name_pitch_jobs_only():
    import re
    k = _real_kind("screen_pitch")
    assert k["lead"] == "screen_lead" and k["match_in"] == "title" and not k.get("lead_review")
    for yes in ("Fill the pitch for EOSE", "Dig the catalysts for RMBS", "Pitch memo for MU",
                "Fill the pitch for ENCORE", "Dig catalysts for SCORE"):   # tickers holding 'core' stay in
        assert re.search(k["match"], yes, re.IGNORECASE)
    for no in ("Run the gem hunt and pitch the top five", "Shortlist five names", "Write the AMD memo",
               "Pitch the core screen to Stott", "Initiate coverage with a pitch", "Add it to the watchlist",
               "Pitch five names", "Pitch the top ideas to Stott", "Pitch deck for 3 names"):
        assert not re.search(k["match"], no, re.IGNORECASE)


CLEAN_PITCH = "# Pitch: ZZZZ\nRevenue grew 40% in FY24 [S1]. Backlog doubled [S2]."


async def test_pip_fills_a_clean_pitch_without_waking_scout(office, erb):
    off, llm, cfg = office
    cfg["kinds"]["pitch"] = _real_kind("screen_pitch")
    llm.script("screen_associate",
               tool_turn(("write_file", {"ticker": "ZZZZ", "path": "pitch.md", "content": CLEAN_PITCH})),
               tool_turn(("submit_result", {**DONE, "findings": "ZZZZ pitch filled, sources logged."})))
    tid = off.assign("screen_lead", "Fill the pitch for ZZZZ")
    await off.idle()
    assert off.store.task(tid)["assignee"] == "screen_associate" and polish_of(off, tid) is None
    assert tid in off.reported and not [c for c in llm.calls if c["who"] == "screen_lead"]


async def test_a_pitch_that_states_a_price_target_goes_to_scout(office, erb):
    off, llm, cfg = office
    cfg["kinds"]["pitch"] = _real_kind("screen_pitch")
    llm.script("screen_associate",
               tool_turn(("write_file", {"ticker": "ZZZZ", "path": "pitch.md",
                                         "content": CLEAN_PITCH + "\nOur price target is $64."})),
               tool_turn(("submit_result", DONE)))
    llm.script("screen_lead", text_turn("Cut the target, kept the evidence."))
    tid = off.assign("screen_lead", "Fill the pitch for ZZZZ")
    await off.idle()
    p = polish_of(off, tid)
    assert p["assignee"] == "screen_lead" and p["escalation_reason"] == "audit_flag"
    assert tid not in off.reported


def test_the_shipped_client_relations_kinds_are_narrow_and_always_finish_with_harbor():
    import re
    news, mail = _real_kind("cr_newsletter"), _real_kind("cr_outreach")
    for k in (news, mail):
        assert k["lead"] == "cr_lead" and k["lead_review"] is True and k["match_in"] == "title"
    for yes in ("Draft this week's newsletter", "Write the weekly note"):
        assert re.search(news["match"], yes, re.IGNORECASE)
    for no in ("Fix the issue with the outbox", "Build the slidedeck newsletter", "Draft an outreach email to Jo",
               "Write the RMBS memo", "Finalize the newsletter", "Send this week's newsletter", "Approve the weekly note"):
        assert not re.search(news["match"], no, re.IGNORECASE)
    for yes in ("Draft an outreach email to Jo", "Email Jo at Acme about the note"):
        assert re.search(mail["match"], yes, re.IGNORECASE)
    for no in ("Suppress this contact: jo@x.com", "Plan the outreach approach", "Unsubscribe Jo from outreach",
               "Draft this week's newsletter", "Build the slidedeck", "Finalize the outreach email to Jo",
               "Send outreach email 4", "Reply to the email from Acme", "Check email deliverability",
               "Why did the email bounce"):
        assert not re.search(mail["match"], no, re.IGNORECASE)


NEWS = {"title": "Week one", "x_post": "New note is up.", "linkedin_post": "Our new weekly note is up.",
        "body": "## What we are watching\nWe are tracking how the market digests the quarter."}


async def test_wren_fixes_a_refused_draft_and_harbor_still_finalizes(office):
    off, llm, cfg = office
    cfg["kinds"]["news"] = {**_real_kind("cr_newsletter"), "match": "newsletter"}
    bad = {**NEWS, "body": NEWS["body"] + "\nThis stock is guaranteed to double, so buy now."}
    llm.script("cr_associate", tool_turn(("save_newsletter", bad)),
               tool_turn(("save_newsletter", NEWS)),
               tool_turn(("submit_result", {**DONE, "findings": "Issue saved, gate clean."})))
    llm.script("cr_lead", text_turn("Read it, edited it, finalizing next."))
    tid = off.assign("cr_lead", "Draft this week's newsletter")
    await off.idle()
    p = polish_of(off, tid)
    assert off.store.task(tid)["assignee"] == "cr_associate" and "Fix every error" in str(
        off.store.events_where(task_ids=[tid], types=["tool_result"], limit=10))
    assert p["assignee"] == "cr_lead" and p["escalation_reason"] == "lead_review"   # fixed, so not a failure
    assert "Issue saved, gate clean." in p["body"]


async def test_a_draft_that_never_clears_the_gate_is_a_real_escalation(office):
    off, llm, cfg = office
    cfg["kinds"]["news"] = {**_real_kind("cr_newsletter"), "match": "newsletter"}
    bad = {**NEWS, "body": NEWS["body"] + "\nThis stock is guaranteed to double, so buy now."}
    llm.script("cr_associate", tool_turn(("save_newsletter", bad)), tool_turn(("save_newsletter", bad)),
               text_turn("The gate keeps refusing my draft."))
    llm.script("cr_lead", text_turn("Taking it from here."))
    tid = off.assign("cr_lead", "Draft this week's newsletter")
    await off.idle()
    assert polish_of(off, tid)["escalation_reason"] == "gate_fails"


async def test_a_pitch_left_with_verify_markers_goes_to_scout(office, erb):
    off, llm, cfg = office
    cfg["kinds"]["pitch"] = _real_kind("screen_pitch")
    llm.script("screen_associate",
               tool_turn(("write_file", {"ticker": "ZZZZ", "path": "pitch.md",
                                         "content": CLEAN_PITCH + "\nBacklog [VERIFY: source]."})),
               tool_turn(("submit_result", DONE)))
    llm.script("screen_lead", text_turn("Filled the open spots."))
    tid = off.assign("screen_lead", "Fill the pitch for ZZZZ")
    await off.idle()
    assert polish_of(off, tid)["escalation_reason"] == "audit_note"


def test_the_save_tools_still_say_what_the_config_counts_as_a_failed_gate():
    from hq.tools import client
    meta = {"id": "x", "status": "draft", "words": 1,
            "problems": [{"level": "error", "where": "body", "msg": "no"}]}
    for report in (client._report(meta), client._email_report(meta)):
        for name in ("cr_newsletter", "cr_outreach"):
            assert all(m in report["next"] for m in _real_kind(name)["fail_markers"])
    clean = {**meta, "problems": []}
    assert not any(m in client._report(clean)["next"] for m in _real_kind("cr_newsletter")["fail_markers"])
