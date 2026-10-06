"""Slidedeck tools for Client Relations. The copy is the words; code fills in every number
and builds the PowerPoint. Nothing here calls the Anthropic API."""

from __future__ import annotations

import asyncio
import json
import re

from hq import deck, deckbuild, deckpack, handoff, outbox
from hq.engine.guards import GuardBlock
from hq.tools.desk import TICK, _schema, _ticker
from hq.tools.office import Tool, ToolContext

COPY_SHAPE = {
    "subtitle": "one line for the cover",
    "slides": {
        "thesis": {"headline": "an action title, 16 words at most",
                   "bullets": [{"text": "one claim, 34 words at most", "detail": "(optional second line)",
                                "source": "report:01_investment_summary"}],
                   "notes": "speaker notes"},
        "call": {"headline": "...", "note": "(one sentence under the cards)", "notes": "..."},
        "(data slides: drivers, forecast, valuation, sensitivity, simulation, wrong, assumptions, sources, disclosures)":
            {"headline": "...", "notes": "..."},
    },
}


async def _slidedeck_material(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    r = handoff.readiness(ctx.office, t)
    if not all(c["ok"] for c in r["conditions"]):
        raise GuardBlock("Not ready for a slidedeck: " + "; ".join(c["detail"] for c in r["conditions"] if not c["ok"])
                         + " Ask the owner through their delegate with relay_request.")
    sections = {k: len(v.split()) for k, v in deck.coverage_sections(t).items()}
    return json.dumps({
        "ticker": t,
        "slides": [{"id": i, "filled_by": who, "job": job} for i, _, who, job in deck.SLIDES],
        "words_slides": "headline + 3 to 5 bullets {text, detail?, source} + notes. Sources: report:<section> or brief:<part>.",
        "data_slides": "code fills the numbers and charts; you write the headline (and a one-sentence note on call and valuation) and the notes. "
                       "drivers, wrong and sensitivity use Quant's own words from the brief.",
        "report_sections": sections,
        "rules": ["Every figure you write must be in the report, the Model Brief or the model, exactly as stated there.",
                  "Targets, ratings and upside only for approved names, with the ticker in the same sentence.",
                  "Risks and what-would-prove-us-wrong together must weigh at least as much as thesis, mispricing and catalysts.",
                  "Measured and institutional: no superlatives, urgency, exclamation marks, em dashes or advice."],
        "copy_shape": COPY_SHAPE,
        "next": "Read the sections you need with read_file, the brief with read_model_brief, then save_slidedeck_copy.",
    }, indent=1)


def _report(problems: list[dict], extra: dict) -> str:
    errs = deck.errors(problems)
    return json.dumps({**extra, "errors": [f"{p['where']}: {p['msg']}" for p in errs],
                       "warnings": [f"{p['where']}: {p['msg']}" for p in problems if p["level"] == "warning"],
                       "next": ("Fix every error and save again." if errs else
                                "Clean. The lead can build_slidedeck.")}, indent=1)


async def _save_slidedeck_copy(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    c = inp.get("copy")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except ValueError as e:
            raise GuardBlock(f"`copy` is not valid JSON: {e}") from e
    if not isinstance(c, dict):
        raise GuardBlock("`copy` must be an object with `subtitle` and `slides`.")
    by = ctx.office.agents[ctx.agent.id].nickname
    path, problems = await asyncio.to_thread(deck.save_copy, ctx.office, t, c, by=by)
    return _report(problems, {"saved": f"{path.parent.name}/copy.json", "ticker": t})


async def _read_slidedeck_copy(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    saved = deck.load_copy(ctx.office, t)
    if saved is None:
        raise GuardBlock(f"No saved slidedeck copy for {t} today. Save one with save_slidedeck_copy.")
    return json.dumps({"by": saved["by"], "date": saved["date"], "model_version": saved["model_version"],
                       "clean": saved["clean"], "problems": saved["problems"], "copy": saved["copy"]}, indent=1)


async def _build_slidedeck(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    saved = deck.load_copy(ctx.office, t)
    if saved is None:
        raise GuardBlock(f"No saved slidedeck copy for {t} today. Save one with save_slidedeck_copy first.")
    out = deck.deck_dir(ctx.office, t) / f"{deck.file_stem(ctx.office, t, 'Slidedeck')}.pptx"
    try:
        await asyncio.to_thread(deckbuild.build, ctx.office, t, saved["copy"], out)
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    return json.dumps({"built": f"slidedecks/{t}/{out.parent.name}/{out.name}", "slides": len(deck.SLIDES),
                       "next": "Check the slidedeck, then the lead calls finalize_slidedeck (with the matching newsletter issue)."}, indent=1)


async def _finalize_slidedeck(ctx: ToolContext, inp: dict) -> str:
    """Package the slidedeck (build, tie out every figure, PDF) and put it, with its matching newsletter, on the Captain's desk."""
    from hq.publish import get_publisher
    from hq.tools.client import _issue

    office = ctx.office
    t = _ticker(inp)
    note = str(inp.get("note") or "").strip()[:1500]
    issue = None
    if inp.get("issue"):
        issue = _issue(ctx, {"issue": inp["issue"]})
        if issue["status"] in outbox.LOCKED:
            raise GuardBlock(f"Issue {issue['id']} is {outbox.LOCKED[issue['status']]}.")
        body = (outbox.issue_dir(office.outbox_dir, issue["id"]) / "draft.md").read_text()
        if not re.search(rf"\b{re.escape(t)}\b", f"{issue.get('title', '')} {body}"):
            raise GuardBlock(f"Issue {issue['id']} does not mention {t}, so it is not this slidedeck's matching note.")
        errors = [p for p in outbox.recheck(office, issue["id"]) if p["level"] == "error"]
        if errors:
            raise GuardBlock("The matching newsletter can't go yet:\n- " + "\n- ".join(f"{p['where']}: {p['msg']}" for p in errors))
    try:
        meta = await asyncio.to_thread(deckpack.package, office, t)
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    files = list(meta["files"].values())
    publisher = None
    if issue:
        publisher = get_publisher()
        outbox.set_status(office.outbox_dir, issue["id"], "finalizing")
        try:
            issue = await asyncio.to_thread(publisher.prepare, office, issue["id"])
        except Exception as e:   # the publisher's own failure must leave the issue editable
            outbox.set_status(office.outbox_dir, issue["id"], "draft")
            raise GuardBlock(f"Building the newsletter files failed: {e}") from e
        files += list(issue["files"].values())
    summary = (f"{t} slidedeck: {meta['slides']} slides, built from Quant model v{meta['model_version']} and the initiating-coverage report. "
               f"Tally's code tied {meta['matched']} of {meta['figures']} printed figures to a source (the source map is attached). "
               f"Files: {', '.join(k.replace('_', ' ') for k in meta['files'])}"
               + ("; no PDF, because PowerPoint could not export one" if "pdf" not in meta["files"] else "")
               + (f". The matching weekly note, \"{issue['title']}\", is attached and approved with it." if issue else ". No matching newsletter is attached.")
               + (f"\n\n{note}" if note else "")
               + "\n\nNothing is sent or posted anywhere. On approval the files are marked ready.")
    approval_id = office.request_approval(
        ctx.agent.id, kind="deck", title=f"{t} slidedeck", summary=summary,
        payload={"ticker": t, "deck": meta["id"], "version": meta["model_version"], "issue": issue["id"] if issue else None,
                 "publisher": publisher.name if publisher else None,
                 "attachments": [f"/outbox/{f}" for f in files]},
        task_id=ctx.task["id"])
    deckpack.set_status(office, meta["id"], "awaiting", approval_id=approval_id, issue=issue["id"] if issue else None)
    if issue:
        outbox.set_status(office.outbox_dir, issue["id"], "awaiting", approval_id=approval_id, publisher=publisher.name)
    office.bus.publish("outbox_ready", ctx.agent.id, ctx.task["id"], deck=meta["id"], title=meta["title"], approval=approval_id)
    return (f"The {t} slidedeck is packaged ({', '.join(meta['files'])}) and approval #{approval_id} is on {office.captain_name}'s desk. "
            "The decision arrives as a message; wrap up.")


FINALIZE_DECK = Tool(
    "finalize_slidedeck", "Finish the slidedeck: build it from today's clean copy, tie every printed figure to a source, export the "
    "PDF, and put the package on {captain}'s desk. Pass `issue` (a saved newsletter draft naming the same ticker) to approve the matching "
    "weekly note together with the slidedeck. Refused while any figure ties to nothing or anything fails its check.",
    _schema({**TICK, "issue": {"type": "string", "description": "The matching newsletter issue id (optional but expected)."},
             "note": {"type": "string", "description": "Anything {captain} should know (optional)."}}, ["ticker"]),
    _finalize_slidedeck)

DECK_MATERIAL = Tool("slidedeck_material", "Everything you need to write a slidedeck for a ready name: the slide catalogue "
                     "(which slides code fills and which you write), the report sections and their sizes, the rules, and "
                     "the copy format.", _schema(TICK, ["ticker"]), _slidedeck_material)
SAVE_DECK_COPY = Tool(
    "save_slidedeck_copy", "Save (or revise) the slidedeck's words and run the gate. Returns errors to fix and warnings. Every figure "
    "must be in the report, the Model Brief or the model; every bullet names its source.",
    _schema({**TICK, "copy": {"type": "object", "description": "{subtitle, slides: {<slide id>: {...}}} as slidedeck_material shows"}},
            ["ticker", "copy"]), _save_slidedeck_copy)
READ_DECK_COPY = Tool("read_slidedeck_copy", "Read today's saved slidedeck copy back, with its check results.",
                      _schema(TICK, ["ticker"]), _read_slidedeck_copy)
BUILD_DECK = Tool("build_slidedeck", "Build the PowerPoint from today's saved, clean copy. Numbers and charts come from the approved "
                  "model; the file lands in the Outbox folder for slidedecks.", _schema(TICK, ["ticker"]), _build_slidedeck)


def deck_tools(tier: str) -> list[Tool]:
    """Wren drafts the copy and assembles; Harbor also builds and signs off."""
    tools = [DECK_MATERIAL, SAVE_DECK_COPY, READ_DECK_COPY, BUILD_DECK]
    return tools + [FINALIZE_DECK] if tier == "lead" else tools
