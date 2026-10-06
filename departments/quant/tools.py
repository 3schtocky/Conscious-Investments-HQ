"""Quant tools: draft assumptions, build the formula workbook, run simulations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from HQ.engine.guards import GuardBlock
from HQ.tools.desk import TICK, _brief, _erb_failure, _schema, _ticker, coverage_dir, run_erb
from HQ.tools.office import Tool, ToolContext


async def _draft_assumptions(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    path = coverage_dir(t) / "assumptions.yaml"
    if path.exists() and not inp.get("overwrite"):
        return (f"assumptions.yaml already exists for {t}; read it with read_file and edit it with "
                "write_file (keep a `# why:` reason on every change). Pass overwrite=true to redraft.")
    code, out = await run_erb("model", t, "--init", *(["--force"] if inp.get("overwrite") else []))
    if code or not path.exists():
        raise GuardBlock(_erb_failure(f"Drafting assumptions for {t}", out)
                         + " (Has erb_facts been run for it?)")
    return f"Drafted assumptions.yaml for {t} from the facts pack (starts at the Street).\n{_brief(out, 2500)}"


async def _build_model(ctx: ToolContext, inp: dict) -> str:
    from departments.quant.workbook import build_model

    t = _ticker(inp)
    src = coverage_dir(t) / "assumptions.yaml"
    if not src.exists():
        raise GuardBlock(f"No assumptions.yaml for {t}. Draft it with draft_assumptions first.")
    store = ctx.office.store
    by = ctx.office.agents[ctx.agent.id].nickname
    async with ctx.office.model_lock(t):   # one build per ticker at a time: versions never collide
        version = store.next_model_version(t)
        out = ctx.office.quant_dir / t / f"{t}_model_v{version}.xlsx"
        frozen = out.with_name(f"{t}_model_v{version}_assumptions.yaml")   # what this version used
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_text(src.read_text())
        result = await asyncio.to_thread(build_model, frozen, out, version=version,
                                         prepared_by=f"{by}, Quant")
        summary = {k: result[k] for k in ("price_targets", "total_returns", "rating", "price",
                                          "warnings", "as_of", "target_date")}
        summary["check"] = {"ok": result["ok"], "formulas": result["formulas"],
                            "compared": result["compared"], "problems": result["problems"]}
        summary["assumptions"] = str(frozen)
        store.add_model(ticker=t, version=version, path=str(out), created_by=ctx.agent.id,
                        summary=summary)
    ctx.office.bus.publish("model_built", ctx.agent.id, ctx.task["id"], ticker=t, version=version,
                           ok=result["ok"], path=str(out))
    status = ("Formula check PASSED: every output matches the valuation engine."
              if result["ok"] else "Formula check FAILED:\n- " + "\n- ".join(result["problems"][:10]))
    return json.dumps({"ticker": t, "version": version, "file": f"quant/{out.name}",
                       "price_targets": summary["price_targets"], "rating": summary["rating"],
                       "total_returns": summary["total_returns"], "price": summary["price"],
                       "warnings": summary["warnings"], "formulas": result["formulas"],
                       "check": status}, indent=1, default=float)


async def _run_simulations(ctx: ToolContext, inp: dict) -> str:
    from departments.quant.simulate import drivers, monte_carlo, write_to_workbook
    from departments.quant.workbook import load_assumptions

    t = _ticker(inp)
    versions = ctx.office.store.models(t)
    if not versions:
        raise GuardBlock(f"No model for {t} yet. Build it with build_model first.")
    m = versions[-1]
    if m["status"] not in ("draft", "changes"):
        raise GuardBlock(f"v{m['version']} is {m['status']}; its workbook is frozen. Build a new "
                         "version first, then simulate that.")
    frozen = Path(m["summary"].get("assumptions") or coverage_dir(t) / "assumptions.yaml")
    a, _ = load_assumptions(frozen)   # the inputs this version was built from
    runs = min(max(int(inp.get("runs") or 2000), 200), 10_000)
    mc = await asyncio.to_thread(monte_carlo, a, runs)
    drv = await asyncio.to_thread(drivers, a)
    await asyncio.to_thread(write_to_workbook, Path(m["path"]), mc, drv)
    pct = mc["percentiles"]
    return json.dumps({
        "ticker": t, "version": m["version"], "runs": mc["n"],
        "price_target_p10_p50_p90": [round(pct[10], 2), round(pct[50], 2), round(pct[90], 2)],
        "chance_above_price": round(mc["p_above_price"], 3),
        "chance_beats_sp500_benchmark": round(mc["p_beats_benchmark"], 3),
        "top_value_drivers": [{"driver": d["driver"], "pt_range": [round(d["pt_low"], 2),
                                                                   round(d["pt_high"], 2)]}
                              for d in drv[:4]],
        "note": "Written to the workbook's Monte Carlo and Value Drivers tabs.",
    }, indent=1)


DRAFT_ASSUMPTIONS = Tool(
    "draft_assumptions", "Draft assumptions.yaml from the facts pack (calibrated to the Street). "
    "Then edit it with write_file, with a reason on every change.",
    _schema({**TICK, "overwrite": {"type": "boolean"}}, ["ticker"]), _draft_assumptions)


BUILD_MODEL = Tool(
    "build_model", "Build the next version of the Excel model from assumptions.yaml: Inputs, "
    "calculation tabs per scenario and Outputs, all live formulas, then check every output "
    "against the valuation engine. Registers the version as a draft.",
    _schema(TICK, ["ticker"]), _build_model)


RUN_SIMULATIONS = Tool(
    "run_simulations", "Monte Carlo on the latest model version plus one-at-a-time value drivers; "
    "results go into the workbook and come back as a summary.",
    _schema({**TICK, "runs": {"type": "integer", "description": "200 to 10,000 (default 2,000)"}},
            ["ticker"]), _run_simulations)

