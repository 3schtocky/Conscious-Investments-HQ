"""Fewer wasted turns: forgiving file paths, helpful errors, search, and results that stick."""

from __future__ import annotations

import json

import pytest
from conftest import text_turn, tool_turn

from hq.engine.guards import GuardTripped, TaskGuard


@pytest.fixture
def coverage(monkeypatch, tmp_path):
    """A throwaway ticker folder, so no test writes into the real research files."""
    monkeypatch.setattr("hq.tools.desk.ERB_DIR", tmp_path / "erb")
    root = tmp_path / "erb" / "coverage" / "ZZZZ"
    (root / "facts" / "filings").mkdir(parents=True)
    (root / "facts" / "facts.md").write_text("# Facts\nRevenue grew 40%.\nCash is $120 million.\n")
    (root / "facts" / "filings" / "10-K_2025-12-31_mdna.txt").write_text(
        "\n".join(f"line {i} about nothing" for i in range(1, 500))
        + "\nThe Series B preferred stock carries a 9% coupon.\n")
    (root / "brief.md").write_text("# Brief\n")
    return root


def _seen(llm, who="Quill", call=1):
    return [c for c in llm.calls if c["who"] == who][call]["params"]["messages"][-1]["content"]


async def _go(office, llm, who, *calls):
    llm.script(who, tool_turn(*calls), text_turn("done"))
    office.assign(who, "x")
    await office.idle()
    return _seen(llm, who)


# file paths ---------------------------------------------------------------------------------
async def test_paths_with_the_ticker_or_coverage_prefix_still_work(make_office, coverage):
    office, llm = make_office()
    r = await _go(office, llm, "Quill",
                  ("read_file", {"ticker": "ZZZZ", "path": "ZZZZ/facts/facts.md"}),
                  ("read_file", {"ticker": "ZZZZ", "path": "coverage/ZZZZ/facts/facts.md"}),
                  ("write_file", {"ticker": "ZZZZ", "path": "ZZZZ/notes/idea.md", "content": "hi"}))
    assert "Revenue grew 40%" in r[0]["content"] and "Revenue grew 40%" in r[1]["content"]
    assert "Saved notes/idea.md" in r[2]["content"]
    assert (coverage / "notes" / "idea.md").read_text() == "hi"


async def test_a_wrong_file_name_comes_back_with_the_real_ones(make_office, coverage):
    office, llm = make_office()
    r = await _go(office, llm, "Quill",
                  ("read_file", {"ticker": "ZZZZ", "path": "facts/filings/10-K_2025-12-31_risk_factors.txt"}))
    msg = r[0]["content"]
    assert r[0]["is_error"]
    assert "Closest: facts/filings/10-K_2025-12-31_mdna.txt" in msg
    assert "facts/facts.md" in msg and "brief.md" in msg


async def test_missing_folder_says_to_build_the_facts_first(make_office, coverage):
    office, llm = make_office()
    r = await _go(office, llm, "Quill", ("read_file", {"ticker": "NOPE", "path": "brief.md"}))
    assert "no files for NOPE yet" in r[0]["content"] and "erb_facts" in r[0]["content"]


async def test_other_wings_are_never_told_about_quants_private_files(make_office, coverage, tmp_path):
    office, llm = make_office()
    (office.quant_dir / "ZZZZ").mkdir(parents=True)
    (office.quant_dir / "ZZZZ" / "secret_draft.md").write_text("draft")
    r = await _go(office, llm, "Quill", ("read_file", {"ticker": "ZZZZ", "path": "nope.md"}))
    assert "secret_draft" not in r[0]["content"]


# reading and searching -----------------------------------------------------------------------
async def test_one_read_is_capped_and_says_where_to_continue(make_office, coverage):
    office, llm = make_office()
    (coverage / "wide.txt").write_text("\n".join("x" * 900 for _ in range(100)))
    r = await _go(office, llm, "Quill", ("read_file", {"ticker": "ZZZZ", "path": "wide.txt", "lines": 400}))
    text = r[0]["content"]
    assert len(text) < 22_000
    assert "continue at offset" in text and "stopped at 20,000 characters" in text


async def test_search_finds_the_line_and_where_to_read(make_office, coverage):
    office, llm = make_office()
    r = await _go(office, llm, "Quill",
                  ("search_file", {"ticker": "ZZZZ", "pattern": "series b preferred"}),
                  ("search_file", {"ticker": "ZZZZ", "pattern": "cash", "path": "facts/facts.md"}),
                  ("search_file", {"ticker": "ZZZZ", "pattern": "zzz-not-there"}),
                  ("search_file", {"ticker": "ZZZZ", "pattern": "(unclosed"}))
    assert "facts/filings/10-K_2025-12-31_mdna.txt:500: The Series B preferred stock carries a 9% coupon." in r[0]["content"]
    assert "facts/facts.md:3: Cash is $120 million." in r[1]["content"]
    assert "No lines match" in r[2]["content"]
    assert not r[3].get("is_error")   # a bad regex is matched as plain words, not an error


async def test_search_caps_the_hits(make_office, coverage):
    office, llm = make_office()
    r = await _go(office, llm, "Quill", ("search_file", {"ticker": "ZZZZ", "pattern": "about nothing"}))
    text = r[0]["content"]
    assert text.count("10-K_2025-12-31_mdna.txt:") == 25
    assert "474 more matches" in text   # 499 lines, 25 shown


def test_every_wing_that_reads_files_can_search_them():
    from hq.tools.office import tools_for

    for aid, tier, wing in (("er_lead", "lead", "equity_research"), ("er_associate", "associate", "equity_research"),
                            ("quant_associate", "associate", "quant"), ("screen_lead", "lead", "screening"),
                            ("audit_lead", "lead", "audit"), ("cr_lead", "lead", "client_relations")):
        assert "search_file" in [t.name for t in tools_for(tier, aid, wing)], aid


# submit_result -------------------------------------------------------------------------------
async def test_submit_result_forgives_how_open_questions_are_written(make_office):
    office, llm = make_office()
    llm.script("Quill", tool_turn(("delegate", {"to": "Ledger", "job": "j1"})),
               tool_turn(("delegate", {"to": "Ledger", "job": "j2"})), text_turn("done"))
    llm.script("Ledger",
               tool_turn(("submit_result", {"findings": "A", "confidence": "high",
                                            "open_questions": "Is the warrant exercisable?"})),
               tool_turn(("submit_result", {"findings": "B", "confidence": "high",
                                            "open_questions": [{"question": "Cash runway?", "why": "no 10-Q"}]})))
    office.assign("Quill", "x")
    await office.idle()
    first, second = (json.loads(c["content"]) for c in
                     [_seen(llm, "Quill", 1)[0], _seen(llm, "Quill", 2)[0]])
    assert first["open_questions"] == ["Is the warrant exercisable?"]
    assert second["open_questions"] == ["Cash runway?; no 10-Q"]


async def test_a_long_result_is_accepted_up_to_the_new_limit(make_office):
    office, llm = make_office()
    llm.script("Quill", tool_turn(("delegate", {"to": "Ledger", "job": "j"})), text_turn("done"))
    llm.script("Ledger", tool_turn(("submit_result", {"findings": "x" * 12_000, "confidence": "high"})))
    office.assign("Quill", "x")
    await office.idle()
    assert len(json.loads(_seen(llm, "Quill", 1)[0]["content"])["findings"]) == 12_000


# the loop guard ------------------------------------------------------------------------------
def test_rebuilding_after_a_write_is_not_a_loop():
    g = TaskGuard(max_turns=99, cost_cap=99, repeat_tool_calls=3)
    build = ("build_model", {"ticker": "EOSE"})
    g.on_tool_call(*build)
    g.on_tool_call(*build)
    g.on_tool_call("write_file", {"ticker": "EOSE", "path": "assumptions.yaml", "content": "v2"})
    g.on_tool_call(*build)   # would have been the third identical call
    g.on_tool_call(*build)
    g.on_tool_call("write_file", {"ticker": "EOSE", "path": "assumptions.yaml", "content": "v3"})
    g.on_tool_call(*build)


def test_without_a_write_in_between_it_is_still_a_loop():
    g = TaskGuard(max_turns=99, cost_cap=99, repeat_tool_calls=3)
    build = ("build_model", {"ticker": "EOSE"})
    g.on_tool_call(*build)
    g.on_tool_call(*build)
    with pytest.raises(GuardTripped, match="build_model"):
        g.on_tool_call(*build)


def test_writing_the_same_file_again_and_again_is_still_a_loop():
    g = TaskGuard(max_turns=99, cost_cap=99, repeat_tool_calls=3)
    write = ("write_file", {"ticker": "EOSE", "path": "brief.md", "content": "same"})
    g.on_tool_call(*write)
    g.on_tool_call(*write)
    with pytest.raises(GuardTripped):
        g.on_tool_call(*write)
