"""The Model Brief: Quant's one-page answer to "what is in this model that matters?".

Code builds the facts straight from the approved model's own assumptions and the valuation
engine (targets, what each method says, the drivers ranked by swing, sensitivity, Monte Carlo),
so nothing is retyped. Sigma and Delta then add the reading: the view, why each driver matters
and what would break the model. A brief is `ready` only when the facts exist for the approved
version and the reading passes `check_reading`. Client Relations reads this brief instead of the
workbook, and no deck starts without it.

Files: Quant/<T>/<T>_model_v<N>_brief.json (facts and reading) and .md (the same, to read).
Nothing here calls the Anthropic API.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from hq.engine.runtime import Office

MIN_DRIVERS, MAX_DRIVERS = 3, 5
SCENARIOS = ("bear", "base", "bull")
MC_RUNS = 2000
_USD = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)(\s?(?:bn|mn|m|b|k|billion|million|thousand|trillion))?\b", re.IGNORECASE)


def brief_path(office: Office, ticker: str, version: int) -> Path:
    return office.quant_dir / ticker / f"{ticker}_model_v{version}_brief.json"


def _round(x, n=2):
    return None if x is None else round(float(x), n)


def _projection_rows(df) -> list[dict]:
    """A scenario's yearly projection as plain rows (fiscal year, revenue, margin, EPS, free cash flow)."""
    df = df if "fy" in df.columns else df.reset_index()
    return [{"fy": int(r["fy"]), "revenue": _round(r["revenue"], 0), "revenue_growth": _round(r["revenue_growth"], 4),
             "ebitda": _round(r["ebitda"], 0), "ebitda_margin": _round(r["ebitda_margin"], 4),
             "net_income": _round(r["net_income"], 0), "eps": _round(r["eps"]), "ufcf": _round(r["ufcf"], 0)}
            for r in df.to_dict("records")]


def build_facts(office: Office, ticker: str, version: int) -> dict:
    """The model's facts, computed by the same engine that built the workbook."""
    from erb import model as erb_model

    from hq.quant.simulate import drivers, monte_carlo
    from hq.quant.workbook import load_assumptions

    m = office.store.model(ticker, version)
    if m is None:
        raise ValueError(f"No model v{version} for {ticker}.")
    if m["status"] != "approved":
        raise ValueError(f"{ticker} v{version} is {m['status']}: a brief is written for the "
                         f"approved version only.")
    a, _ = load_assumptions(Path(m["summary"]["assumptions"]))
    s = m["summary"]
    scenarios, paths = {}, {}
    for name in SCENARIOS:
        r = erb_model.value_scenario(a, name)
        paths[name] = _projection_rows(r["projections"])
        scenarios[name] = {
            "price_target": _round(r["price_target"]), "total_return": _round(r["total_return"], 4),
            "price_return": _round(r["price_return"], 4),
            "methods": {k: _round(v) for k, v in r["methods"].items()},
            "weights": {k: _round(v, 3) for k, v in r["weights"].items()},
            "deltas": {k: _round(v, 4) for k, v in r["deltas"].items() if isinstance(v, (int, float))},
        }
    drv = drivers(a)
    mc = monte_carlo(a, MC_RUNS)
    sens = erb_model.sensitivity(a)
    rate = erb_model.rating(a, scenarios["base"]["total_return"])
    n = int(a["projection_years"])
    return {
        "ticker": ticker, "version": version, "rating": s["rating"], "price": _round(s["price"]),
        "as_of": s["as_of"], "target_date": s["target_date"], "horizon_months": a.get("horizon_months", 15),
        "benchmark_return": _round(rate["benchmark_return"], 4), "band": rate["band"],
        "excess_return": _round(rate["excess_return"], 4),
        "scenarios": scenarios,
        "projections": {"base": paths["base"],
                        "revenue": {k: [row["revenue"] for row in paths[k]] for k in SCENARIOS}},
        "drivers": [{"driver": d["driver"], "low_case": d["low_case"], "high_case": d["high_case"],
                     "pt_low": _round(d["pt_low"]), "pt_high": _round(d["pt_high"]),
                     "swing": _round(d["swing"]), "base": _round(d["base"])} for d in drv],
        "sensitivity": {"rows_wacc": [_round(x, 4) for x in sens["rows_wacc"]],
                        "cols": [_round(x, 3) for x in sens["cols"]], "col_label": sens["col_label"],
                        "values": [[_round(v) for v in row] for row in sens["values"]]},
        "monte_carlo": {"runs": mc["n"], "percentiles": {str(k): _round(v) for k, v in mc["percentiles"].items()},
                        "p_above_price": _round(mc["p_above_price"], 3),
                        "p_beats_benchmark": _round(mc["p_beats_benchmark"], 3)},
        "inputs": {"revenue_growth": [_round(x, 4) for x in erb_model._vec(a["revenue"]["growth"], n)]
                   if "growth" in a["revenue"] else None,
                   "ebitda_margin": [_round(x, 4) for x in erb_model._vec(a["margins"]["ebitda_margin"], n)],
                   "wacc": _round(erb_model.wacc(a, erb_model.scenario(a, "base"))["wacc"], 4),
                   "terminal_method": a["terminal"].get("method", "exit_multiple"),
                   "exit_ev_ebitda": a["terminal"].get("exit_ev_ebitda"),
                   "weights": a.get("weights")},
        "warnings": s.get("warnings", []),
    }


def allowed_dollars(facts: dict) -> set[float]:
    """Every dollar figure the brief's reading may quote."""
    out = {facts["price"]}
    for sc in facts["scenarios"].values():
        out.add(sc["price_target"])
        out.update(v for v in sc["methods"].values() if v is not None)
    for d in facts["drivers"]:
        out.update(x for x in (d["pt_low"], d["pt_high"], d["swing"], d["base"]) if x is not None)
    out.update(v for v in facts["monte_carlo"]["percentiles"].values() if v is not None)
    out.update(v for row in facts["sensitivity"]["values"] for v in row if v is not None)
    return {float(x) for x in out if x is not None}


def check_reading(facts: dict, reading: dict) -> list[str]:
    """Problems with Quant's reading (empty = clean). Dollar figures must be the model's own."""
    problems: list[str] = []
    text = lambda k: reading.get(k) if isinstance(reading.get(k), str) else ""
    if len(text("view").split()) < 25:
        problems.append("`view` should be a short paragraph (at least 25 words): what the model says "
                        "and why the rating follows.")
    drv = reading.get("drivers")
    if not isinstance(drv, list) or not (MIN_DRIVERS <= len(drv) <= MAX_DRIVERS):
        problems.append(f"`drivers` needs {MIN_DRIVERS} to {MAX_DRIVERS} entries ({{driver, why}}).")
        drv = []
    names = [d["driver"] for d in facts["drivers"]]
    for i, d in enumerate(drv):
        if not isinstance(d, dict) or not str(d.get("driver", "")).strip() or not str(d.get("why", "")).strip():
            problems.append(f"drivers[{i}] needs both `driver` and `why`.")
        elif d["driver"] not in names:
            problems.append(f"drivers[{i}].driver '{d['driver']}' is not one of the model's drivers: "
                            + "; ".join(names))
    sc = reading.get("scenarios")
    if not isinstance(sc, dict) or any(not str(sc.get(k, "")).strip() for k in SCENARIOS):
        problems.append("`scenarios` needs plain-English text for bear, base and bull.")
        sc = {}
    breaks = reading.get("breaks")
    if not isinstance(breaks, list) or not [b for b in breaks if str(b).strip()]:
        problems.append("`breaks` needs at least one observable thing that would break the model "
                        "(a number or event someone can check).")
        breaks = []
    if not text("sensitivity_note").strip():
        problems.append("`sensitivity_note` should say in a sentence or two what the sensitivity table shows.")
    allowed = allowed_dollars(facts)
    blob = " ".join([text("view"), text("sensitivity_note"), *(str(b) for b in breaks),
                     *(str(v) for v in sc.values()), *(str(d.get("why", "")) for d in drv if isinstance(d, dict))])
    for num, scale in _USD.findall(blob):
        if scale.strip():
            continue   # $2.1bn of revenue and the like are not model outputs
        v = float(num.replace(",", ""))
        if not any(abs(v - x) < 0.006 or abs(v - round(x)) < 0.006 for x in allowed):
            problems.append(f"${num} is not a figure from this model's brief. Quote only the targets, "
                            "driver prices, simulation percentiles and sensitivity values it lists.")
    return problems


def load(office: Office, ticker: str, version: int) -> dict | None:
    p = brief_path(office, ticker, version)
    return json.loads(p.read_text()) if p.is_file() else None


def status(office: Office, ticker: str) -> dict:
    """Where the approved version's brief stands: none | facts | ready."""
    m = office.store.approved_model(ticker)
    if m is None:
        return {"state": "none", "version": None, "detail": f"{ticker} has no approved model."}
    b = load(office, ticker, m["version"])
    if b is None:
        return {"state": "none", "version": m["version"],
                "detail": f"No Model Brief for v{m['version']} yet. Quant writes it."}
    if not b.get("reading"):
        return {"state": "facts", "version": m["version"],
                "detail": f"v{m['version']} has the facts but no reading from Quant yet."}
    problems = check_reading(b["facts"], b["reading"])
    if problems:
        return {"state": "facts", "version": m["version"], "detail": "; ".join(problems)}
    return {"state": "ready", "version": m["version"], "detail": f"v{m['version']} brief is ready."}


def ensure_facts(office: Office, ticker: str, version: int) -> dict:
    """The saved brief for this version, building its facts first if there are none."""
    b = load(office, ticker, version)
    if b is None:
        b = {"facts": build_facts(office, ticker, version), "reading": None}
        _write(office, ticker, version, b)
    return b


def save_reading(office: Office, ticker: str, version: int, reading: dict, *, by: str) -> tuple[dict, list[str]]:
    b = ensure_facts(office, ticker, version)
    problems = check_reading(b["facts"], reading)
    if not problems:
        b["reading"] = {**{k: reading[k] for k in ("view", "drivers", "scenarios", "sensitivity_note", "breaks")},
                        "by": by}
        _write(office, ticker, version, b)
    return b, problems


def _write(office: Office, ticker: str, version: int, b: dict) -> None:
    p = brief_path(office, ticker, version)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(b, indent=1))
    p.with_suffix(".md").write_text(render(b))


def render(b: dict) -> str:
    f, r = b["facts"], b.get("reading")
    usd = lambda x: "n/a" if x is None else f"${x:,.2f}"
    pct = lambda x: "n/a" if x is None else f"{x * 100:+.1f}%"
    sc = f["scenarios"]
    lines = [f"# {f['ticker']} Model Brief (Quant model v{f['version']})", "",
             f"Rating **{f['rating']}**. Price {usd(f['price'])} on {f['as_of']}; targets for {f['target_date']}.", "",
             "| Case | Price target | Total return |", "|---|---|---|"]
    lines += [f"| {k.title()} | {usd(sc[k]['price_target'])} | {pct(sc[k]['total_return'])} |" for k in SCENARIOS]
    lines += ["", (f"Benchmark return over the horizon {pct(f['benchmark_return'])}; base case beats it by "
                   f"{pct(f['excess_return'])} (Outperform needs more than {f['band'] * 100:.0f} points)."), "",
              "## What moves the price target", "",
              "| Driver | Low | High | Swing |", "|---|---|---|---|"]
    lines += [f"| {d['driver']} | {d['low_case']}: {usd(d['pt_low'])} | {d['high_case']}: {usd(d['pt_high'])} "
              f"| {usd(d['swing'])} |" for d in f["drivers"]]
    mc = f["monte_carlo"]
    p = mc["percentiles"]
    lines += ["", f"## Simulation ({mc['runs']:,} runs)", "",
              (f"10th to 90th percentile target: {usd(p['10'])} to {usd(p['90'])}; median {usd(p['50'])}. "
               f"Chance the target is above today's price {mc['p_above_price'] * 100:.0f}%; "
               f"chance of beating the benchmark {mc['p_beats_benchmark'] * 100:.0f}%.")]
    if f["warnings"]:
        lines += ["", "## Model warnings", ""] + [f"- {w}" for w in f["warnings"]]
    if r:
        lines += ["", f"## Quant's reading ({r.get('by', 'Quant')})", "", r["view"], "", "### Drivers", ""]
        lines += [f"- **{d['driver']}**: {d['why']}" for d in r["drivers"]]
        lines += ["", "### The three cases", ""] + [f"- **{k.title()}**: {r['scenarios'][k]}" for k in SCENARIOS]
        lines += ["", "### Sensitivity", "", r["sensitivity_note"], "", "### What would break it", ""]
        lines += [f"- {x}" for x in r["breaks"]]
    else:
        lines += ["", "*Quant's reading is not written yet.*"]
    return "\n".join(lines) + "\n"
