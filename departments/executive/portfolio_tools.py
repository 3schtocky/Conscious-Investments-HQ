"""Paper portfolio tools: read the scoreboard, propose an entry, propose an exit.

Every entry and exit is an approval card; nothing changes until the Captain decides, and no real
money is involved. The rules live in `departments.executive.portfolio` and are enforced in code.
"""

from __future__ import annotations

import json

from departments.executive import portfolio
from HQ.engine.guards import GuardBlock
from HQ.tools.office import Tool, ToolContext, _str

PROPOSERS = {"er_lead", "quant_lead", "chief_of_staff"}   # who may put a position in front of the Captain


async def _prices(ctx: ToolContext, extra: list[str] | None = None) -> dict:
    return await portfolio.fetch_prices(ctx.office, extra)


def _pct(x: float | None) -> float | None:
    return None if x is None else round(x * 100, 1)


async def _read_portfolio(ctx: ToolContext, inp: dict) -> str:
    s = portfolio.scoreboard(ctx.office, await _prices(ctx))
    row = lambda r: {"ticker": r["ticker"], "size_pct": r["size_pct"], "entered": r["entry_day"],
                     "entry_price": round(r["entry_price"], 2), "price": r["price"] and round(r["price"], 2),
                     "return_pct": _pct(r["return"]), "vs_sp500_pct": _pct(r["vs_spy"]),
                     "base_target": r["base_target"], "rating": r["rating"]}
    return json.dumps({
        "summary": portfolio.summary_text(s), "value_usd": round(s["value"], 2), "cash_usd": round(s["cash"], 2),
        "return_pct": _pct(s["return"]) if s["priced"] else None,
        "sp500_return_pct": _pct(s["benchmark_return"]) if s["priced"] else None,
        "open_positions": [row(r) for r in s["open"]],
        "closed_positions": [{**row(r), "exited": r["exit_day"], "why": r["exit_reason"]} for r in s["closed"][:10]],
        "rules": [f"Entries: approved Outperform names only, sized {', '.join(str(x) for x in portfolio.SIZES)} percent.",
                  ("Exits: code flags a reached base target, a rating below Outperform, or a 25% loss; "
                  f"{ctx.office.captain_name} decides.")]}, indent=1)


READ_PORTFOLIO = Tool(
    "read_portfolio",
    "The paper portfolio and its scoreboard against the S&P 500: value, cash, return since the "
    "first position, each open position with its return and distance to the base target, and "
    "recent exits. These are the only performance figures the office may quote.",
    {"type": "object", "properties": {}, "required": []}, _read_portfolio)


async def _propose_position(ctx: ToolContext, inp: dict) -> str:
    from HQ.tools.desk import _ticker

    t = _ticker(inp)
    size = inp.get("size_pct")
    if not isinstance(size, int) or isinstance(size, bool):
        raise GuardBlock("`size_pct` must be 3, 5 or 8.")
    thesis = _str(inp, "thesis", max_len=1500)
    try:
        approval_id = ctx.office.propose_position(ctx.agent.id, t, size, thesis, task_id=ctx.task["id"],
                                                  prices=await _prices(ctx, [t]))
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    return (f"The {t} entry ({size}% of the portfolio) is on {ctx.office.captain_name}'s desk as approval "
            f"#{approval_id}. Nothing is entered until he approves; wrap up.")


PROPOSE_POSITION = Tool(
    "propose_position",
    "Propose a paper-portfolio entry for {captain} to approve. Only a name whose approved model "
    "rates it Outperform qualifies. Pick the size by conviction (3, 5 or 8 percent of the "
    "portfolio) and argue it in the thesis: why now, what would prove it wrong, and why that "
    "size. The fill is on paper, at the latest price, when {captain} approves.",
    {"type": "object",
     "properties": {"ticker": {"type": "string", "description": "US ticker, e.g. RMBS"},
                    "size_pct": {"type": "integer", "enum": list(portfolio.SIZES)},
                    "thesis": {"type": "string", "description": "The case, the risk, and why this size."}},
     "required": ["ticker", "size_pct", "thesis"]},
    _propose_position)


async def _propose_exit(ctx: ToolContext, inp: dict) -> str:
    from HQ.tools.desk import _ticker

    t = _ticker(inp)
    reason = _str(inp, "reason", max_len=1500)
    try:
        approval_id = ctx.office.propose_exit(ctx.agent.id, t, reason, task_id=ctx.task["id"],
                                              prices=await _prices(ctx, [t]))
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    return (f"The {t} exit is on {ctx.office.captain_name}'s desk as approval #{approval_id}. The position "
            "stays open until he approves.")


PROPOSE_EXIT = Tool(
    "propose_exit",
    "Propose closing a paper-portfolio position, with the reason: the thesis broke, the model "
    "changed, or the money has a better use. {captain} approves the exit or holds.",
    {"type": "object",
     "properties": {"ticker": {"type": "string", "description": "US ticker, e.g. RMBS"},
                    "reason": {"type": "string"}},
     "required": ["ticker", "reason"]},
    _propose_exit)


def portfolio_tools(agent_id: str) -> list[Tool]:
    """Everyone can read the scoreboard; Research, Quant and the Chief of Staff can propose."""
    return [READ_PORTFOLIO, PROPOSE_POSITION, PROPOSE_EXIT] if agent_id in PROPOSERS else [READ_PORTFOLIO]
