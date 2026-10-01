"""Phase 8: the paper portfolio. Entry and exit rules, paper fills on approval, exit flags from
code, mark-to-market and the scoreboard against the S&P 500."""

from __future__ import annotations

import json

import pytest
from conftest import register_model, text_turn, tool_turn

from hq import portfolio

PRICES = {"RMBS": 100.0, "META": 50.0, "SPY": 500.0}


def _results(llm, who, call=1):
    return [c for c in llm.calls if c["who"] == who][call]["params"]["messages"][-1]["content"]


def approve_model(office, ticker, rating="Outperform", base=130.0, version=1):
    office.store.add_model(ticker=ticker, version=version, path=f"/tmp/{ticker}.xlsx", created_by="quant_lead",
                           summary={"price_targets": {"bear": base * 0.6, "base": base, "bull": base * 1.4},
                                    "rating": rating, "check": {"ok": True}})
    office.store.set_model_status(ticker, version, "approved")


@pytest.fixture
async def office_llm(make_office, monkeypatch):
    office, llm = make_office()
    approve_model(office, "RMBS")
    prices = dict(PRICES)
    monkeypatch.setattr("hq.quotes.latest", lambda tickers: {t: prices.get(t) for t in tickers})
    office.test_prices = prices
    for who in ("Quill", "Juno", "Sigma"):        # each decision is sent back to whoever asked
        llm.script(who, *[text_turn("Noted.") for _ in range(30)])
    yield office, llm
    await office.idle()


def enter(office, ticker="RMBS", size=5, prices=None):
    card = office.propose_position("er_lead", ticker, size, "Thesis.", task_id=None, prices=prices)
    office.decide(card, "approved", prices=prices)
    return next(p for p in office.store.positions("open") if p["ticker"] == ticker)


# ---- entry rules ------------------------------------------------------------------------------
async def test_only_approved_outperform_names_in_conviction_tiers(office_llm):
    office, _ = office_llm
    register_model(office, "META", 1)                               # a draft, never approved
    approve_model(office, "AMD", rating="Neutral")
    refuse = lambda *a: pytest.raises(ValueError, match=a[-1])
    with refuse("no approved model"):
        office.propose_position("er_lead", "META", 5, "x", task_id=None)
    with refuse("rates it Neutral"):
        office.propose_position("er_lead", "AMD", 5, "x", task_id=None)
    with refuse("3, 5 or 8 percent"):
        office.propose_position("er_lead", "RMBS", 10, "x", task_id=None)
    office.test_prices["RMBS"] = None
    with refuse("No price for RMBS"):
        office.propose_position("er_lead", "RMBS", 5, "x", task_id=None)
    office.test_prices["RMBS"] = 100.0

    card = office.propose_position("er_lead", "RMBS", 8, "Design wins are converting.", task_id=None)
    c = office.store.approval(card)
    assert c["kind"] == "portfolio" and c["title"] == "Enter RMBS at 8%"
    assert "about $8,000.00 at the latest price of $100.00" in c["summary"]
    assert "base price target of $130.00" in c["summary"] and "No real money moves" in c["summary"]
    assert c["payload"]["audit"] == []                               # Tally's check agrees with the model
    with refuse("already waiting for a decision"):
        office.propose_position("quant_lead", "RMBS", 3, "x", task_id=None)
    assert office.store.positions() == []                            # nothing until the Captain approves


async def test_approval_is_a_paper_fill_at_the_last_close(office_llm):
    office, _ = office_llm
    p = enter(office, size=8)
    assert (p["shares"], p["entry_price"], p["entry_value"], p["spy_at_entry"]) == (80.0, 100.0, 8000.0, 500.0)
    assert portfolio.cash(office) == 92_000.0 and portfolio.value(office, PRICES) == 100_000.0
    with pytest.raises(ValueError, match="already holds RMBS"):
        office.propose_position("er_lead", "RMBS", 3, "x", task_id=None)
    assert {e["type"] for e in office.store.events(limit=100)} >= {"portfolio_changed"}


async def test_an_entry_that_no_longer_qualifies_is_refused_and_the_card_stays_open(office_llm):
    office, _ = office_llm
    card = office.propose_position("er_lead", "RMBS", 5, "x", task_id=None)
    office.store.set_model_status("RMBS", 1, "superseded")           # Quant withdrew the rating
    with pytest.raises(ValueError, match="no approved model"):
        office.decide(card, "approved")
    assert office.store.approval(card)["status"] == "pending" and office.store.positions() == []
    office.decide(card, "rejected")
    assert office.store.positions() == []


async def test_cash_is_never_negative(office_llm):
    office, _ = office_llm
    for i in range(12):                                             # 12 x 8% = 96% invested
        t = f"T{chr(65 + i)}"
        approve_model(office, t)
        office.test_prices[t] = 10.0
        enter(office, t, 8)
    approve_model(office, "LAST")
    office.test_prices["LAST"] = 10.0
    with pytest.raises(ValueError, match="Not enough cash"):
        office.propose_position("er_lead", "LAST", 5, "x", task_id=None)
    assert enter(office, "LAST", 3)["entry_value"] == pytest.approx(3000.0)
    assert portfolio.cash(office) == pytest.approx(1000.0)


# ---- scoreboard and marks ---------------------------------------------------------------------
async def test_scoreboard_against_the_sp500(office_llm):
    office, _ = office_llm
    assert office.portfolio_view()["return"] is None                 # nothing to score yet
    assert "no positions yet" in office.portfolio_view()["summary"]
    enter(office, size=5)
    now = {"RMBS": 120.0, "SPY": 510.0}
    s = office.portfolio_view(now)
    assert s["value"] == pytest.approx(101_000.0) and s["cash"] == 95_000.0
    assert s["return"] == pytest.approx(0.01) and s["benchmark_return"] == pytest.approx(0.02)
    assert s["vs_benchmark"] == pytest.approx(-0.01)                  # cash drag shows honestly
    [row] = s["open"]
    assert row["return"] == pytest.approx(0.20) and row["vs_spy"] == pytest.approx(0.18)
    assert row["to_target"] == pytest.approx(130 / 120 - 1) and row["weight"] == pytest.approx(6000 / 101_000)
    assert (s["picks_beating"], s["picks_judged"]) == (1, 1)
    assert s["summary"].startswith(f"Paper portfolio since {office.ledger.today()}: +1.0% against +2.0% for the S&P 500")
    assert "cash, which earns nothing" in s["summary"]


async def test_daily_marks_build_an_indexed_history(office_llm):
    office, _ = office_llm
    assert office.portfolio_tick() == {"marked": False, "flags": []}   # no positions: nothing to mark
    enter(office, size=5)
    assert portfolio.mark(office, PRICES, day="2026-10-01")
    assert portfolio.mark(office, {"RMBS": 110.0, "SPY": 505.0}, day="2026-10-02")
    assert portfolio.mark(office, {"RMBS": 120.0, "SPY": 510.0}, day="2026-10-02")   # same day: updated
    assert portfolio.mark(office, {"RMBS": None, "SPY": 510.0}, day="2026-10-03") is None   # never a false value
    office.store._exec("UPDATE positions SET entry_ts=?", (1790812800.0,))    # entered 2026-09-30
    h = portfolio.history(office)
    assert [m["day"] for m in h] == ["2026-09-30", "2026-10-01", "2026-10-02"]
    assert h[0]["portfolio"] == 100 and h[0]["benchmark"] == 100 and h[0]["entry"]   # indexed at the entry
    assert h[2]["portfolio"] == pytest.approx(101.0) and h[2]["benchmark"] == pytest.approx(102.0)


# ---- exits --------------------------------------------------------------------------------------
async def test_code_raises_exit_cards_and_a_held_flag_is_not_raised_again(office_llm):
    office, _ = office_llm
    enter(office, size=5)
    assert office.portfolio_tick(PRICES)["flags"] == []
    hit = {"RMBS": 131.0, "SPY": 500.0}
    [card] = office.portfolio_tick(hit)["flags"]
    c = office.store.approval(card)
    assert c["title"] == "Exit RMBS: reached its base price target" and c["agent"] == "office"
    assert "code checks (no API cost)" in c["summary"] and "$131.00 has reached the base target $130.00" in c["summary"]
    assert office.portfolio_tick(hit)["flags"] == []                  # one card at a time
    office.decide(card, "rejected", "Let it run.", prices=hit)        # hold
    assert office.store.positions("open")[0]["held_flags"] == ["target:v1"]
    assert office.portfolio_tick(hit)["flags"] == []                  # not raised again
    # a different rule still fires: the rating dropped
    approve_model(office, "RMBS", rating="Neutral", version=2)
    [card2] = office.portfolio_tick(hit)["flags"]
    assert "no longer rated Outperform" in office.store.approval(card2)["title"]
    office.decide(card2, "approved", prices=hit)
    [closed] = office.store.positions("closed")
    assert closed["exit_price"] == 131.0 and closed["exit_approval_id"] == card2
    assert portfolio.cash(office) == pytest.approx(95_000 + 50 * 131.0)
    s = office.portfolio_view(hit)
    assert s["open"] == [] and s["closed"][0]["return"] == pytest.approx(0.31)
    assert s["return"] == pytest.approx(0.0155)


async def test_stop_loss_flag(office_llm):
    office, _ = office_llm
    enter(office, size=3)
    assert office.portfolio_tick({"RMBS": 76.0, "SPY": 500.0})["flags"] == []
    down = {"RMBS": 75.0, "SPY": 500.0}
    [card] = office.portfolio_tick(down)["flags"]
    assert "is down 25% from entry" in office.store.approval(card)["title"]
    office.decide(card, "changes", "What changed?", prices=down)        # a question is not a hold
    [again] = office.portfolio_tick(down)["flags"]
    office.decide(again, "rejected", "Hold.", prices=down)              # a hold silences this level...
    assert office.portfolio_tick({"RMBS": 70.0, "SPY": 500.0})["flags"] == []
    [deeper] = office.portfolio_tick({"RMBS": 59.0, "SPY": 500.0})["flags"]   # ...and re-arms 15 points lower
    assert "-41.0% from the $100.00 entry" in office.store.approval(deeper)["summary"]


# ---- tools --------------------------------------------------------------------------------------
def test_who_may_propose(make_office):
    from hq.tools.office import REQUEST_APPROVAL, definitions, tools_for

    names = lambda tier, aid, wing: {t.name for t in tools_for(tier, aid, wing)}
    for tier, aid, wing in (("lead", "er_lead", "equity_research"), ("lead", "quant_lead", "quant"),
                            ("associate", "chief_of_staff", "executive")):
        assert {"read_portfolio", "propose_position", "propose_exit"} <= names(tier, aid, wing)
    for tier, aid, wing in (("lead", "screen_lead", "screening"), ("lead", "audit_lead", "audit"),
                            ("associate", "er_associate", "equity_research"), ("lead", "cr_lead", "client_relations")):
        got = names(tier, aid, wing)
        assert "read_portfolio" in got and not {"propose_position", "propose_exit"} & got
    [d] = definitions([REQUEST_APPROVAL])
    assert "portfolio" not in d["input_schema"]["properties"]["kind"]["enum"]


async def test_agents_propose_through_the_tools(office_llm):
    office, llm = office_llm
    llm.scripts["Quill"].clear()
    llm.script("Quill",
               tool_turn(("read_portfolio", {}),
                         ("propose_position", {"ticker": "RMBS", "size_pct": 4, "thesis": "x"}),
                         ("request_approval", {"kind": "portfolio", "title": "Buy RMBS", "summary": "x"}),
                         ("propose_exit", {"ticker": "RMBS", "reason": "x"}),
                         ("propose_position", {"ticker": "rmbs", "size_pct": 5, "thesis": "Design wins; 5% until the next print."})),
               text_turn("Proposed."), text_turn("Thanks."))
    office.assign("Quill", "Propose RMBS for the portfolio")
    await office.idle()
    r = _results(llm, "Quill")
    assert "no positions yet" in json.loads(r[0]["content"])["summary"]
    assert r[1]["is_error"] and "3, 5 or 8 percent" in r[1]["content"]
    assert r[2]["is_error"] and "propose_position or propose_exit" in r[2]["content"]
    assert r[3]["is_error"] and "holds no open RMBS position" in r[3]["content"]
    assert "approval #1" in r[4]["content"] and "Nothing is entered until he approves" in r[4]["content"]
    office.decide(1, "approved")
    await office.idle()
    assert [p["ticker"] for p in office.store.positions("open")] == ["RMBS"]
    assert office.store.positions("open")[0]["proposed_by"] == "er_lead"


async def test_newsletter_material_carries_the_scoreboard(office_llm):
    office, llm = office_llm
    enter(office, size=5)
    office.test_prices.update(RMBS=120.0, SPY=510.0)
    llm.script("Wren", tool_turn(("newsletter_material", {})), text_turn("Read."))
    office.assign("Wren", "What can we publish?")
    await office.idle()
    board = json.loads(_results(llm, "Wren")[0]["content"])["scoreboard"]
    assert board.startswith("Paper portfolio since") and "+1.0% against +2.0%" in board


# ---- endpoints ----------------------------------------------------------------------------------
async def test_portfolio_endpoints(office_llm):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, _ = office_llm
    card = office.propose_position("er_lead", "RMBS", 5, "Thesis.", task_id=None)
    with TestClient(create_app(office_factory=lambda: office)) as c:
        view = c.get("/api/portfolio").json()
        assert view["open"] == [] and view["pending"][0]["ticker"] == "RMBS" and view["sizes"] == [3, 5, 8]
        office.test_prices["RMBS"] = None
        bad = c.post(f"/api/approvals/{card}/decide", json={"decision": "approved"})
        assert bad.status_code == 400 and "No price for RMBS" in bad.json()["detail"]
        office.test_prices["RMBS"] = 100.0
        assert c.post(f"/api/approvals/{card}/decide", json={"decision": "approved"}).status_code == 200
        view = c.get("/api/portfolio").json()
        assert view["open"][0]["ticker"] == "RMBS" and view["cash"] == 95_000.0 and view["pending"] == []


# ---- the demo scene -----------------------------------------------------------------------------
async def test_demo_portfolio_scene_uses_the_real_rules(make_office, monkeypatch):
    from hq.demo import DEMO_HOLDING, DemoLLM, prepare_demo_portfolio, scene_portfolio

    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    scene_portfolio(office, llm)                       # no model yet: the scene sits out
    assert office.store.tasks() == [] and not await prepare_demo_portfolio(office)   # offline: no price
    monkeypatch.setattr("hq.quotes.latest", lambda tickers: {t: 100.0 if t != "SPY" else 500.0 for t in tickers})
    assert await prepare_demo_portfolio(office) and await prepare_demo_portfolio(office)   # once
    assert len(office.store.models(DEMO_HOLDING)) == 1
    scene_portfolio(office, llm)
    await office.idle()
    [card] = office.store.approvals("pending")
    assert card["title"] == f"Enter {DEMO_HOLDING} at 5%" and card["payload"]["audit"] == []
    scene_portfolio(office, llm)                       # next loop: still waiting, nothing is re-proposed
    await office.idle()
    assert len(office.store.approvals("pending")) == 1
    office.decide(card["id"], "approved")
    await office.idle()
    scene_portfolio(office, llm)                       # held now: Quill reports the scoreboard
    await office.idle()
    assert office.store.chat("dm:captain|er_lead")[-1]["text"].startswith("Portfolio check (demo): Paper portfolio since")
    types = {e["type"] for e in office.store.events(limit=3000)}
    assert "guard_block" not in types and "task_error" not in types
    assert all(t["status"] == "done" for t in office.store.tasks())


# ---- regressions from the Phase 8 code review ---------------------------------------------------
async def test_missing_quotes_never_produce_a_quotable_return(office_llm):
    office, llm = office_llm
    await_ = enter(office, size=5)
    assert await_["ticker"] == "RMBS"
    out = {"RMBS": None, "SPY": 500.0}
    s = office.portfolio_view(out)
    assert s["priced"] is False and "can't be scored" in s["summary"]
    office.test_prices["RMBS"] = None
    llm.scripts["Quill"].clear()
    llm.script("Quill", tool_turn(("read_portfolio", {}), ("propose_exit", {"ticker": "RMBS", "reason": "Thesis broke."})),
               text_turn("Read."), text_turn("Ok."))
    office.assign("Quill", "Check the portfolio")
    await office.idle()
    r = _results(llm, "Quill")
    assert json.loads(r[0]["content"])["return_pct"] is None
    card = office.store.approvals("pending")[-1]
    assert "no quote right now" in card["summary"] and "$0.00" not in card["summary"]


async def test_a_fill_needs_the_benchmark_quote_too(office_llm):
    office, _ = office_llm
    card = office.propose_position("er_lead", "RMBS", 5, "x", task_id=None)
    with pytest.raises(ValueError, match="No price for SPY"):
        office.decide(card, "approved", prices={"RMBS": 100.0, "SPY": None})
    assert office.store.positions() == [] and office.store.approval(card)["status"] == "pending"


async def test_code_raised_cards_cost_no_model_call(office_llm):
    office, llm = office_llm
    enter(office, size=5)
    await office.idle()
    before = len(llm.calls)
    hit = {"RMBS": 131.0, "SPY": 500.0}
    [card] = office.portfolio_tick(hit)["flags"]
    office.decide(card, "approved", prices=hit)
    await office.idle()
    assert len(llm.calls) == before and office.store.positions("open") == []
    assert office.portfolio_tick(hit)["marked"]          # still marked after the last position closes
