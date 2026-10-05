"""Phase 5: Screening tools, the watchlist and Juno's weekly reminder."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from conftest import text_turn, tool_turn

from hq.tools import desk


def _results(llm, who, call=1):
    return [c for c in llm.calls if c["who"] == who][call]["params"]["messages"][-1]["content"]


@pytest.fixture
def fake_erb(tmp_path, monkeypatch):
    """A throwaway erb coverage tree with one Gems screen run."""
    root = tmp_path / "erb"
    run = root / "coverage" / "_screens" / "2026-09-30-gems"
    run.mkdir(parents=True)
    run.joinpath("screen.csv").write_text(
        "cik,ticker,name,sector,market_cap,composite,acceleration,growth,margin,momentum,yoy_q,yoy_q1,"
        "margin_change,mom_6m,runway_years,loss_maker,factors_used\n"
        "1,BFLY,Butterfly Network,Health Care,2.3e9,0.89,0.88,0.83,0.88,0.98,0.395,0.25,0.077,1.19,9.8,True,4\n"
        "2,OUST,Ouster,Industrials,2.7e9,0.86,0.77,0.93,0.80,0.98,0.559,0.489,0.037,1.17,6.5,True,4\n"
        "3,XXXX,Thin Data,Other,1e9,0.99,,,,0.5,,,,0.1,,False,1\n")
    run.joinpath("signals.json").write_text(json.dumps({"BFLY": [{"filed": "2026-08-01",
                                                                   "items": ["1.01 Material agreement"], "url": "u"}]}))
    monkeypatch.setattr(desk, "ERB_DIR", root)
    monkeypatch.setattr("hq.quotes.latest", lambda tickers: {t: {"BFLY": 10.0, "SPY": 500.0}.get(t, 20.0) for t in tickers})
    return root


def test_screening_tools_by_role():
    from hq.engine.llm import web_tools
    from hq.tools.office import tools_for

    names = lambda tier, aid: [t.name for t in tools_for(tier, aid, "screening")]
    scout, pip = names("lead", "screen_lead"), names("associate", "screen_associate")
    assert {"run_screen", "read_screen", "pitch_memo", "add_to_watchlist"} <= set(scout)
    assert "run_screen" not in pip and "add_to_watchlist" not in pip and "pitch_memo" in pip
    assert web_tools({"id": "claude-haiku-4-5"}, "screening", "screen_associate")   # Pip digs
    assert web_tools({"id": "claude-sonnet-5-5"}, "screening", "screen_lead") == []


async def test_read_screen_skips_thin_rows_and_includes_8k_events(make_office, fake_erb):
    office, llm = make_office()
    llm.script("screen_associate", tool_turn(("read_screen", {"preset": "gems", "top": 5})), text_turn("read"))
    office.assign("screen_associate", "read it")
    await office.idle()
    out = json.loads(_results(llm, "screen_associate")[0]["content"])
    assert out["run"] == "2026-09-30-gems"
    assert [r["ticker"] for r in out["rows"]] == ["BFLY", "OUST"]   # XXXX has 1 of 4 factors
    assert out["recent_8k_events"]["BFLY"][0]["items"] == ["1.01 Material agreement"]


async def test_screening_writes_pitches_but_not_briefs(make_office, fake_erb):
    office, llm = make_office()
    llm.script("screen_associate", tool_turn(("write_file", {"ticker": "BFLY", "path": "pitch.md", "content": "# BFLY"}),
                                ("write_file", {"ticker": "BFLY", "path": "brief.md", "content": "x"})),
               text_turn("ok"))
    office.assign("screen_associate", "write")
    await office.idle()
    r = _results(llm, "screen_associate")
    assert "Saved pitch.md" in r[0]["content"] and "pitch.md or notes" in r[1]["content"]


async def test_watchlist_scorecard_and_send_to_research(make_office, fake_erb):
    office, llm = make_office()
    (fake_erb / "coverage" / "BFLY").mkdir(parents=True, exist_ok=True)
    (fake_erb / "coverage" / "BFLY" / "pitch.md").write_text("# pitch")
    llm.script("screen_lead", tool_turn(("add_to_watchlist", {"ticker": "BFLY", "source": "gems 2026-09-30 #10",
                                                        "thesis": "Growth accelerating 25% to 40%."})),
               text_turn("added"))
    office.assign("screen_lead", "shortlist")
    await office.idle()
    [w] = office.store.watchlist()
    assert (w["ticker"], w["price_at_add"], w["spy_at_add"], w["pitch"]) == ("BFLY", 10.0, 500.0, "pitch.md")
    # a later price: BFLY +20%, SPY +5% -> +15 points vs the market
    [row] = office.watchlist_view({"BFLY": 12.0, "SPY": 525.0})
    assert row["return"] == pytest.approx(0.20) and row["vs_spy"] == pytest.approx(0.15)
    # adding the same name from the same screen again refreshes it instead of duplicating
    office.add_watch(ticker="BFLY", added_by="screen_lead", source="gems 2026-09-30 #10",
                     thesis="Updated thesis.", pitch=None, price=11.0, spy=510.0)
    assert len(office.store.watchlist()) == 1 and office.store.watchlist()[0]["thesis"] == "Updated thesis."

    llm.script("chief_of_staff", text_turn("Routed to Quill."))
    out = office.send_watch_to_research(w["id"])
    await office.idle()
    juno = office.store.task(out["task_id"])
    assert juno["assignee"] == "chief_of_staff" and "BFLY" in juno["body"] and "pitch.md" in juno["body"]
    assert office.store.watch(w["id"])["status"] == "researching"


def test_weekly_reminder_once_per_monday(make_office):
    office, _ = make_office()
    tz = office.ledger.tz
    assert office.weekly_reminder(datetime(2026, 10, 5, 7, 0, tzinfo=tz)) is False    # Monday, too early
    assert office.weekly_reminder(datetime(2026, 10, 5, 9, 0, tzinfo=tz)) is True
    assert office.weekly_reminder(datetime(2026, 10, 5, 15, 0, tzinfo=tz)) is False   # already this week
    assert office.weekly_reminder(datetime(2026, 10, 6, 9, 0, tzinfo=tz)) is False    # Tuesday
    assert office.weekly_reminder(datetime(2026, 10, 12, 9, 0, tzinfo=tz)) is True    # next Monday
    msg = office.store.chat("dm:captain|chief_of_staff")[0]
    assert msg["sender"] == "chief_of_staff" and "screen" in msg["text"]
    assert office.ledger.spent_today() == 0   # plain code: no model call


def test_watchlist_endpoints(make_office, fake_erb):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, llm = make_office()
    wid = office.add_watch(ticker="BFLY", added_by="screen_lead", source="gems #1", thesis="t",
                           pitch=None, price=8.0, spy=480.0)
    llm.script("chief_of_staff", text_turn("ok"))
    with TestClient(create_app(office_factory=lambda: office)) as c:
        [row] = c.get("/api/watchlist").json()
        assert row["price_now"] == 10.0 and row["return"] == pytest.approx(0.25)
        assert c.post(f"/api/watchlist/{wid}/research").status_code == 200
        assert c.post("/api/watchlist/999/research").status_code == 404
        assert c.post(f"/api/watchlist/{wid}/drop").status_code == 200
        assert c.get("/api/watchlist").json() == []


async def test_demo_gems_scene_offline(make_office, fake_erb, monkeypatch):
    """The real-tools Gems dry run, with erb's memo command stubbed (no network)."""
    from hq.demo import DemoLLM, scene_gem_hunt

    async def fake_run_erb(*args, timeout=900):
        if args[0] == "memo":
            t = args[1]
            run = fake_erb / "coverage" / "_screens" / "2026-09-30-gems" / "memos"
            run.mkdir(parents=True, exist_ok=True)
            (run / f"{t}.md").write_text(f"# Pitch Memo: {t}\n[VERIFY: write the thesis]")
            (fake_erb / "coverage" / t).mkdir(parents=True, exist_ok=True)
        return 0, "ok"

    monkeypatch.setattr(desk, "run_erb", fake_run_erb)
    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    scene_gem_hunt(office, llm)
    await office.idle()
    tasks = office.store.tasks()
    assert all(t["status"] == "done" for t in tasks), [(t["assignee"], t["status"]) for t in tasks]
    assert [w["ticker"] for w in office.store.watchlist()] == ["OUST", "BFLY"]   # newest first
    assert (fake_erb / "coverage" / "BFLY" / "pitch.md").exists()
    types = {e["type"] for e in office.store.events(limit=5000)}
    assert "guard_block" not in types and "watchlist_added" in types


async def test_pitch_memo_never_overwrites_a_filled_pitch_and_needs_a_screen(make_office, fake_erb, monkeypatch):
    office, llm = make_office()
    (fake_erb / "coverage" / "BFLY").mkdir(parents=True, exist_ok=True)
    (fake_erb / "coverage" / "BFLY" / "pitch.md").write_text("# BFLY\nFilled-in thesis.")
    llm.script("screen_associate", tool_turn(("pitch_memo", {"ticker": "BFLY", "preset": "gems"}),
                                ("pitch_memo", {"ticker": "BFLY", "preset": "core"})), text_turn("ok"))
    office.assign("screen_associate", "pitch")
    await office.idle()
    r = _results(llm, "screen_associate")
    assert "already has a pitch.md (kept" in r[0]["content"]
    assert (fake_erb / "coverage" / "BFLY" / "pitch.md").read_text() == "# BFLY\nFilled-in thesis."
    assert "No core screen yet" in r[1]["content"]


def test_re_adding_a_dropped_name_brings_it_back(make_office):
    office, _ = make_office()
    wid = office.add_watch(ticker="BFLY", added_by="screen_lead", source="gems #1", thesis="t",
                           pitch=None, price=1.0, spy=1.0)
    office.store.set_watch_status(wid, "dropped")
    assert office.store.watchlist() == []
    office.add_watch(ticker="BFLY", added_by="screen_lead", source="gems #1", thesis="again",
                     pitch=None, price=1.0, spy=1.0)
    assert [w["status"] for w in office.store.watchlist()] == ["watching"]
