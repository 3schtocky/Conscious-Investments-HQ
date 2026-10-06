"""Equity Research tools: erb's data and memo commands, run in the Equity Research submodule."""

from __future__ import annotations

from HQ.engine.guards import GuardBlock
from HQ.tools.desk import TICK, _brief, _erb_failure, _schema, _ticker, coverage_dir, run_erb
from HQ.tools.office import Tool, ToolContext


async def _erb_facts(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    code, out = await run_erb("facts", t, *(["--no-filings"] if inp.get("skip_filings") else []))
    facts = coverage_dir(t) / "facts" / "facts.md"
    if code or not facts.is_file():
        raise GuardBlock(_erb_failure(f"erb facts for {t}", out))
    head = "\n".join(facts.read_text().splitlines()[:120])
    return (f"Facts pack ready: facts/facts.md (provenance in facts/provenance.csv, filing excerpts "
            f"in facts/filings/). First 120 lines:\n\n{head}")


async def _erb_peers(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    peers = inp.get("peers")
    if not isinstance(peers, list) or not 1 <= len(peers) <= 8:
        raise GuardBlock("`peers` must be a list of 1 to 8 tickers.")
    peers = [_ticker({"ticker": p}) for p in peers]
    code, out = await run_erb("peers", t, *peers)
    if code:
        raise GuardBlock(f"erb peers failed:\n{_brief(out, 1500)}")
    return _brief(out, 3500)


async def _erb_memo(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    code, out = await run_erb("memo", t)
    if code:
        raise GuardBlock(f"erb memo failed:\n{_brief(out, 1500)}")
    return f"{_brief(out, 1500)}\nThe memo skeleton is in coverage/{t}; read it with list_files/read_file."


async def _erb_lint(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    _, out = await run_erb("lint", t)
    return _brief(out, 3000)


ERB_FACTS = Tool(
    "erb_facts", "Build the facts pack for a ticker from SEC EDGAR filings and market data "
    "(financials, segments, filing excerpts, multiples history, consensus). Start here.",
    _schema({**TICK, "skip_filings": {"type": "boolean"}}, ["ticker"]), _erb_facts)


ERB_PEERS = Tool("erb_peers", "Market snapshot for a ticker and its peers (multiples side by side).",
                 _schema({**TICK, "peers": {"type": "array", "items": {"type": "string"}}},
                         ["ticker", "peers"]), _erb_peers)


ERB_MEMO = Tool("erb_memo", "Write a one-page pitch memo skeleton for a ticker (snapshot, valuation vs "
                "history, Street view). Fill its narrative after research.",
                _schema(TICK, ["ticker"]), _erb_memo)


ERB_LINT = Tool("erb_lint", "Style and sourcing checks on the ticker's written sections.",
                _schema(TICK, ["ticker"]), _erb_lint)

