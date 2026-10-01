"""The paper portfolio: positions the Captain approved, marked to market against the S&P 500.

No money moves. A position is a paper fill at the latest price when the Captain approves the
card; everything else is arithmetic on free Yahoo prices.

Rules (Stott, 2026-10-01):
- **Entry:** only a name whose approved model rates it Outperform can be proposed.
- **Size:** conviction tiers of 3%, 5% or 8% of the portfolio's value at entry, argued on the
  card; one position per name; cash is never negative.
- **Exit:** code raises an exit card when the base price target is reached, the approved rating
  is no longer Outperform, or the position is down 25% from entry. The Captain approves the
  exit or holds; a held flag is not raised again. Leads can also propose an exit with a reason.

The scoreboard compares the portfolio's return since its first position with the S&P 500 (SPY)
over the same days. Cash earns nothing, which the scoreboard says plainly.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from hq.config import office as office_config

if TYPE_CHECKING:
    from hq.engine.runtime import Office

SIZES = (3, 5, 8)            # conviction tiers, % of portfolio value at entry
STOP_LOSS = 0.25             # down this much from entry raises an exit card
BENCHMARK_DEFAULT = "SPY"
FLAGS = {"target": "reached its base price target", "rating": "is no longer rated Outperform",
         "stop": "is down 25% from entry"}


def _cfg() -> dict:
    cfg = office_config().get("portfolio", {}) or {}
    return {"capital": float(cfg.get("starting_capital_usd", 100_000)),
            "benchmark": cfg.get("benchmark", BENCHMARK_DEFAULT)}


def benchmark() -> str:
    return _cfg()["benchmark"]


def tickers(office: Office) -> list[str]:
    """Everything that needs a price: open positions and the benchmark."""
    return sorted({p["ticker"] for p in office.store.positions("open")} | {benchmark()})


def cash(office: Office) -> float:
    out = _cfg()["capital"]
    for p in office.store.positions():
        out -= p["entry_value"]
        if p["status"] == "closed":
            out += p["shares"] * p["exit_price"]
    return out


def value(office: Office, prices: dict[str, float | None]) -> float:
    """Cash plus open positions at the given prices (entry price where a quote is missing)."""
    total = cash(office)
    for p in office.store.positions("open"):
        total += p["shares"] * (prices.get(p["ticker"]) or p["entry_price"])
    return total


def _outperform(office: Office, ticker: str) -> dict:
    model = office.store.approved_model(ticker)
    if model is None:
        raise ValueError(f"{ticker} has no approved model. Only researched names with a model "
                         f"{office.captain_name} approved can enter the portfolio.")
    rating = str(model["summary"].get("rating"))
    if rating != "Outperform":
        raise ValueError(f"{ticker}'s approved model v{model['version']} rates it {rating}. Only "
                         "Outperform names can be proposed.")
    return model


def check_entry(office: Office, ticker: str, size_pct: int, prices: dict[str, float | None]) -> dict:
    """Everything that must hold for an entry, at proposal time and again at approval."""
    model = _outperform(office, ticker)
    if size_pct not in SIZES:
        raise ValueError(f"Size must be {', '.join(str(s) for s in SIZES[:-1])} or {SIZES[-1]} "
                         "percent of the portfolio.")
    if any(p["ticker"] == ticker for p in office.store.positions("open")):
        raise ValueError(f"The portfolio already holds {ticker}. One position per name.")
    price = prices.get(ticker)
    if not price:
        raise ValueError(f"No price for {ticker} right now, so a paper fill isn't possible. Try again later.")
    total = value(office, prices)
    amount = total * size_pct / 100
    if amount > cash(office) + 0.005:
        raise ValueError(f"Not enough cash: a {size_pct}% position is ${amount:,.2f} and the portfolio "
                         f"has ${cash(office):,.2f} in cash. Propose an exit first, or a smaller size.")
    return {"model": model, "price": float(price), "amount": amount, "portfolio_value": total}


def enter(office: Office, *, ticker: str, size_pct: int, thesis: str, proposed_by: str,
          approval_id: int | None, prices: dict[str, float | None]) -> dict:
    """The paper fill, at the latest price. Raises ValueError if the entry no longer qualifies."""
    ok = check_entry(office, ticker, size_pct, prices)
    spy = prices.get(benchmark())
    if not spy:   # without it this pick could never be compared with the benchmark
        raise ValueError(f"No price for {benchmark()} right now, so the entry can't be scored against "
                         "the S&P 500. Try again later.")
    position_id = office.store.add_position(
        ticker=ticker, shares=ok["amount"] / ok["price"], entry_price=ok["price"],
        entry_value=ok["amount"], size_pct=size_pct, spy_at_entry=float(spy) if spy else None,
        thesis=thesis, proposed_by=proposed_by, model_version=ok["model"]["version"],
        approval_id=approval_id)
    return office.store.position(position_id)


def close(office: Office, position_id: int, *, reason: str, approval_id: int | None,
          prices: dict[str, float | None]) -> dict:
    p = office.store.position(position_id)
    if p["status"] != "open":
        raise ValueError(f"The {p['ticker']} position is already closed.")
    price = prices.get(p["ticker"])
    if not price:
        raise ValueError(f"No price for {p['ticker']} right now, so the exit can't be filled. Try again later.")
    spy = prices.get(benchmark())
    if not spy:
        raise ValueError(f"No price for {benchmark()} right now, so the exit can't be scored against "
                         "the S&P 500. Try again later.")
    office.store.close_position(position_id, exit_price=float(price), reason=reason,
                                spy_at_exit=float(spy) if spy else None, approval_id=approval_id)
    return office.store.position(position_id)


STOP_STEP = 0.15             # after a hold, the stop flag re-arms this much further down


def flag_kind(code: str) -> str:
    return code.split(":")[0]


def exit_flags(office: Office, prices: dict[str, float | None]) -> list[dict]:
    """Open positions the exit rules say the Captain should look at. A flag he decided to hold
    through is not raised again, but it re-arms when the facts change: a new model version, or
    the loss deepening by another 15 points."""
    out = []
    for p in office.store.positions("open"):
        price = prices.get(p["ticker"])
        model = office.store.approved_model(p["ticker"])
        version = model["version"] if model else 0
        reasons = []
        if model is None or str(model["summary"].get("rating")) != "Outperform":
            reasons.append((f"rating:v{version}", "The approved model "
                            + ("was withdrawn." if model is None else
                               f"v{version} now rates it {model['summary'].get('rating')}.")))
        base = model["summary"].get("price_targets", {}).get("base") if model else None
        if price and isinstance(base, (int, float)) and price >= base:
            reasons.append((f"target:v{version}", (f"The price ${price:,.2f} has reached the base target "
                                                  f"${base:,.2f} (model v{version}).")))
        loss = 1 - price / p["entry_price"] if price else 0
        if loss >= STOP_LOSS:
            level = int((loss - STOP_LOSS) / STOP_STEP + 1e-9)
            reasons.append((f"stop:{level}", (f"The price ${price:,.2f} is {-loss * 100:.1f}% from the "
                                             f"${p['entry_price']:,.2f} entry.")))
        for code, detail in reasons:
            if code not in p["held_flags"]:
                out.append({"position": p, "code": code, "detail": detail})
    return out


def _row(office: Office, p: dict, prices: dict[str, float | None]) -> dict:
    bench = prices.get(benchmark())
    is_open = p["status"] == "open"
    last = prices.get(p["ticker"]) if is_open else p["exit_price"]
    spy_end = bench if is_open else p["spy_at_exit"]
    ret = (last / p["entry_price"] - 1) if last else None
    spy_ret = (spy_end / p["spy_at_entry"] - 1) if spy_end and p["spy_at_entry"] else None
    model = office.store.approved_model(p["ticker"])
    base = model["summary"].get("price_targets", {}).get("base") if model else None
    who = p["proposed_by"]
    return {**p, "price": last, "value": p["shares"] * last if last else None, "return": ret,
            "spy_return": spy_ret,
            "vs_spy": ret - spy_ret if ret is not None and spy_ret is not None else None,
            "base_target": base, "rating": model["summary"].get("rating") if model else None,
            "to_target": (base / last - 1) if base and last and is_open else None,
            "proposed_by_name": office.name(who) if who in office.agents else who,
            "entry_day": datetime.fromtimestamp(p["entry_ts"], office.ledger.tz).date().isoformat(),
            "exit_day": (datetime.fromtimestamp(p["exit_ts"], office.ledger.tz).date().isoformat()
                         if p["exit_ts"] else None)}


def scoreboard(office: Office, prices: dict[str, float | None]) -> dict:
    """The numbers the office is judged on. Before the first position there is nothing to score."""
    cfg = _cfg()
    positions = office.store.positions()
    rows = [_row(office, p, prices) for p in positions]
    open_rows = [r for r in rows if r["status"] == "open"]
    total = value(office, prices)
    first = min(positions, key=lambda p: p["entry_ts"]) if positions else None
    bench = prices.get(cfg["benchmark"])
    spy_ret = (bench / first["spy_at_entry"] - 1) if first and bench and first["spy_at_entry"] else None
    ret = total / cfg["capital"] - 1 if first else None
    judged = [r for r in rows if r["vs_spy"] is not None]
    for r in open_rows:
        r["weight"] = (r["value"] / total) if r["value"] and total else None
    return {
        "benchmark": cfg["benchmark"], "starting_capital": cfg["capital"], "value": total,
        "cash": cash(office), "invested": total - cash(office),
        "since": (datetime.fromtimestamp(first["entry_ts"], office.ledger.tz).date().isoformat()
                  if first else None),
        "return": ret, "benchmark_return": spy_ret,
        "vs_benchmark": ret - spy_ret if ret is not None and spy_ret is not None else None,
        "picks_beating": sum(1 for r in judged if r["vs_spy"] > 0), "picks_judged": len(judged),
        "average_pick_vs_benchmark": (sum(r["vs_spy"] for r in judged) / len(judged)) if judged else None,
        "open": sorted(open_rows, key=lambda r: -(r["value"] or 0)),
        "closed": sorted((r for r in rows if r["status"] == "closed"), key=lambda r: -r["exit_ts"]),
        "sizes": list(SIZES), "note": "Cash earns nothing in this comparison.",
        # False when a quote is missing: the totals then use entry prices and must not be quoted.
        "priced": bool(all(prices.get(r["ticker"]) for r in open_rows) and (bench or not positions)),
    }


def mark(office: Office, prices: dict[str, float | None], day: str | None = None) -> dict | None:
    """Record today's mark-to-market (one row per day, updated through the day)."""
    positions = office.store.positions()
    bench = prices.get(benchmark())
    if not positions or not bench:
        return None
    if any(not prices.get(p["ticker"]) for p in positions if p["status"] == "open"):
        return None   # a missing quote would record a false value; wait for the next tick
    day = day or office.ledger.today()
    office.store.save_mark(day, value=value(office, prices), cash=cash(office), benchmark=float(bench))
    return office.store.marks(1)[-1]


def history(office: Office) -> list[dict]:
    """The portfolio and the benchmark as two series indexed to 100 at the first position's
    entry (the same base the scoreboard uses), then one point per daily mark: one axis."""
    positions = office.store.positions()
    marks = office.store.marks(2000)
    if not positions or not marks:
        return []
    first = min(positions, key=lambda p: p["entry_ts"])
    v0, b0 = _cfg()["capital"], first["spy_at_entry"]
    if not b0:
        return []
    start = datetime.fromtimestamp(first["entry_ts"], office.ledger.tz).date().isoformat()
    return [{"day": start, "portfolio": 100.0, "benchmark": 100.0, "value": v0, "entry": True}] + [
        {"day": m["day"], "portfolio": 100 * m["value"] / v0, "benchmark": 100 * m["benchmark"] / b0,
         "value": m["value"]} for m in marks if m["day"] >= start]


async def fetch_prices(office: Office, extra: list[str] | None = None) -> dict[str, float | None]:
    """Quotes for the open positions, the benchmark and any extra names, off the event loop."""
    import asyncio

    from hq import quotes

    names = sorted(set(tickers(office)) | {t for t in (extra or []) if t})
    return await asyncio.to_thread(quotes.latest, names)


def summary_text(s: dict) -> str:
    """One plain sentence for the newsletter and for agents."""
    pct = lambda x: f"{x * 100:+.1f}%"
    if s["return"] is None:
        return "The paper portfolio has no positions yet, so there is nothing to score."
    if not s["priced"]:
        return ("Prices are unavailable right now, so the paper portfolio can't be scored. Leave "
                "performance figures out until they are back.")
    line = (f"Paper portfolio since {s['since']}: {pct(s['return'])}"
            + (f" against {pct(s['benchmark_return'])} for the S&P 500 ({s['benchmark']})"
               if s["benchmark_return"] is not None else "")
            + f". {len(s['open'])} open position{'s' if len(s['open']) != 1 else ''}, "
              f"{s['cash'] / s['value'] * 100:.0f}% in cash, which earns nothing in this comparison.")
    if s["picks_judged"]:
        line += f" {s['picks_beating']} of {s['picks_judged']} picks are ahead of the S&P 500 since entry."
    return line


