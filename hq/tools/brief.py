"""Model Brief and hand-off tools. Quant writes the brief; Client Relations reads it and checks
whether a name is ready for a slidedeck. Plain code behind each tool; no model calls."""

from __future__ import annotations

import asyncio
import json

from hq import handoff, modelbrief
from hq.engine.guards import GuardBlock
from hq.tools.desk import TICK, _schema, _ticker
from hq.tools.office import Tool, ToolContext, _str


def _approved(ctx: ToolContext, t: str) -> dict:
    m = ctx.office.store.approved_model(t)
    if m is None:
        raise GuardBlock(f"{t} has no approved model, so there is nothing to brief. A Model Brief is "
                         "written for the version the Captain approved.")
    return m


async def _read_model_brief(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    m = _approved(ctx, t)
    st = modelbrief.status(ctx.office, t)
    if ctx.agent.wing != "quant" and st["state"] != "ready":
        raise GuardBlock(f"The Model Brief for {t} v{m['version']} is not ready ({st['detail']}). Ask Quant "
                         "for it through their delegate with relay_request; do not read the workbook instead.")
    try:
        if ctx.agent.wing == "quant":
            b = await asyncio.to_thread(modelbrief.ensure_facts, ctx.office, t, m["version"])
        else:
            b = modelbrief.load(ctx.office, t, m["version"])
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    st = modelbrief.status(ctx.office, t)   # again: Quant's read may have just built the facts
    return json.dumps({"ticker": t, "version": m["version"], "state": st["state"], "detail": st["detail"],
                       "facts": b["facts"], "reading": b.get("reading")}, indent=1, default=float)


async def _save_model_brief(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    m = _approved(ctx, t)
    reading = {"view": _str(inp, "view", max_len=2500), "drivers": inp.get("drivers"),
               "scenarios": inp.get("scenarios"), "sensitivity_note": _str(inp, "sensitivity_note", max_len=1200),
               "breaks": inp.get("breaks")}
    by = ctx.office.agents[ctx.agent.id].nickname
    try:
        _, problems = await asyncio.to_thread(modelbrief.save_reading, ctx.office, t, m["version"], reading, by=by)
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    if problems:
        return json.dumps({"saved": False, "fix": problems}, indent=1)
    card = await asyncio.to_thread(handoff.maybe_offer, ctx.office, t)
    out = {"saved": True, "ticker": t, "version": m["version"],
           "file": f"quant/{t}_model_v{m['version']}_brief.md"}
    if card:
        out["handoff"] = (f"All four conditions hold: the hand-off card is on {ctx.office.captain_name}'s desk "
                          f"(approval #{card}). Nothing starts until he approves.")
    else:
        missing = [c["detail"] for c in handoff.readiness(ctx.office, t)["conditions"] if not c["ok"]]
        out["handoff"] = ("Saved. The hand-off card is filed once everything is in place."
                          + (" Still missing: " + "; ".join(missing) if missing else ""))
    return json.dumps(out, indent=1)


async def _slidedeck_readiness(ctx: ToolContext, inp: dict) -> str:
    r = handoff.readiness(ctx.office, _ticker(inp))
    r["missing"] = [c["detail"] for c in r["conditions"] if not c["ok"]]
    return json.dumps(r, indent=1)


READ_MODEL_BRIEF = Tool(
    "read_model_brief",
    "Quant's Model Brief for a ticker's approved model: the facts computed from the model (targets, what "
    "each method says, drivers ranked by swing, sensitivity, simulation) and Quant's reading of them. "
    "Quant also gets the facts before the reading is written. Other wings can read only a finished brief.",
    _schema(TICK, ["ticker"]), _read_model_brief)

SAVE_MODEL_BRIEF = Tool(
    "save_model_brief",
    "Write the reading for the approved model's brief, from read_model_brief's facts: the view in one "
    "short paragraph; 3 to 5 drivers ({driver, why}) named exactly as the facts name them; plain English "
    "for bear, base and bull; what the sensitivity table shows; and observable things that would break "
    "the model. Dollar figures must be ones the facts list. Returns what to fix, or saves it and, when "
    "the name is ready, files the hand-off card for {captain}.",
    _schema({**TICK, "view": {"type": "string"},
             "drivers": {"type": "array", "items": {"type": "object", "properties": {
                 "driver": {"type": "string"}, "why": {"type": "string"}}, "required": ["driver", "why"]}},
             "scenarios": {"type": "object", "properties": {k: {"type": "string"} for k in modelbrief.SCENARIOS},
                           "required": list(modelbrief.SCENARIOS)},
             "sensitivity_note": {"type": "string"},
             "breaks": {"type": "array", "items": {"type": "string"}}},
            ["ticker", "view", "drivers", "scenarios", "sensitivity_note", "breaks"]),
    _save_model_brief)

DECK_READINESS = Tool(
    "slidedeck_readiness",
    "Is a name ready for a slidedeck? Lists the four conditions (approved Outperform model, finished "
    "initiating-coverage report, Quant's Model Brief, no earlier hand-off) and what is missing. If "
    "something is missing, ask its owner through their delegate with relay_request.",
    _schema(TICK, ["ticker"]), _slidedeck_readiness)
