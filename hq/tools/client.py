"""Client Relations tools: the newsletter and client-ready memos.

Wren drafts; Harbor finalizes. Every draft passes the code gate in `hq.outbox.check_issue`
(only approved numbers, no hype, no advice) before it can reach the Captain, and nothing leaves
the Outbox without his approval. Nothing here calls the Anthropic API or posts anywhere.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from hq import outbox
from hq.engine.guards import GuardBlock
from hq.tools.office import Tool, ToolContext, _str


def _issue(ctx: ToolContext, inp: dict) -> dict:
    issue_id = _str(inp, "issue", max_len=60)
    try:
        return outbox.load(ctx.office.outbox_dir, issue_id)
    except KeyError as e:
        raise GuardBlock(str(e.args[0])) from e


def _report(meta: dict) -> dict:
    errors = [p for p in meta["problems"] if p["level"] == "error"]
    warnings = [p for p in meta["problems"] if p["level"] == "warning"]
    return {"issue": meta["id"], "status": meta["status"], "words": meta["words"],
            "errors": [f"{p['where']}: {p['msg']}" for p in errors],
            "warnings": [f"{p['where']}: {p['msg']}" for p in warnings],
            "next": ("Fix every error and save again." if errors
                     else "Clean. The Client Relations lead can finalize it with finalize_newsletter.")}


# newsletter_material ---------------------------------------------------------------------------
async def _newsletter_material(ctx: ToolContext, inp: dict) -> str:
    from hq import quotes

    office = ctx.office
    pub = outbox.publishable(office)
    tickers = sorted(set(pub["watch"]) | {"SPY"}) if pub["watch"] else []
    prices = await asyncio.to_thread(quotes.latest, tickers) if tickers else {}
    researched = []
    for t, m in pub["approved"].items():
        on = (datetime.fromtimestamp(m["approved_at"], office.ledger.tz).date().isoformat()
              if m["approved_at"] else None)
        researched.append({"ticker": t, "rating": m["rating"], "price_targets": m["targets"],
                           "cite_as": f"Quant model v{m['version']}, approved {on}"})
    watching = [{"ticker": w["ticker"], "thesis": w["thesis"], "source": w["source"],
                 "flagged_on": datetime.fromtimestamp(w["added"], office.ledger.tz).date().isoformat(),
                 "return_since_flagged": None if w["return"] is None else round(w["return"], 4),
                 "vs_sp500": None if w["vs_spy"] is None else round(w["vs_spy"], 4),
                 "status": w["status"]} for w in office.watchlist_view(prices)]
    from hq import portfolio

    board = portfolio.scoreboard(office, await portfolio.fetch_prices(office))
    scoreboard = (portfolio.summary_text(board) if board["return"] is not None else
                  "The paper portfolio has no positions yet. Leave the scoreboard out.")
    return json.dumps({
        "researched_names": researched, "watchlist": watching,
        "scoreboard": scoreboard,
        "rules": ["Price targets and ratings only for researched_names, exactly as given, with the cite_as line.",
                  ("Watchlist names are ideas we are watching: thesis and returns since flagged, never a "
                   "valuation, a target, a rating or upside."),
                  "No hype, no personal advice, no emojis, no em dashes.",
                  ("About 400 to 600 words: one lead idea, a short what-we-are-watching list, and (once it "
                   "exists) the scoreboard. The disclaimer is added for you.")]}, indent=1)


NEWSLETTER_MATERIAL = Tool(
    "newsletter_material",
    "Everything the office may publish right now: names with a {captain}-approved model (rating "
    "and price targets, with the line to cite), the watchlist (theses and returns since flagged), "
    "and the house rules. Start every newsletter here; anything not in this list stays out.",
    {"type": "object", "properties": {}, "required": []}, _newsletter_material)


# save_newsletter -------------------------------------------------------------------------------
async def _save_newsletter(ctx: ToolContext, inp: dict) -> str:
    title = _str(inp, "title", max_len=90)
    body = _str(inp, "body", max_len=outbox.BODY_LIMIT)
    x_post = _str(inp, "x_post", max_len=600)
    linkedin_post = _str(inp, "linkedin_post", max_len=3000)
    issue_id = _str(inp, "issue", required=False, max_len=60) or None
    try:
        meta = outbox.save_draft(ctx.office, agent_id=ctx.agent.id, title=title, body=body,
                                 x_post=x_post, linkedin_post=linkedin_post, issue_id=issue_id)
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    except KeyError as e:
        raise GuardBlock(str(e.args[0])) from e
    ctx.office.bus.publish("outbox_draft", ctx.agent.id, ctx.task["id"], issue=meta["id"],
                           title=title, status=meta["status"], words=meta["words"])
    return json.dumps(_report(meta), indent=1)


SAVE_NEWSLETTER = Tool(
    "save_newsletter",
    "Save a newsletter draft to the Outbox. To revise an existing issue (a draft, or one "
    "{captain} sent back), pass its id as `issue`. Give the title, the body in Markdown without the title or a disclaimer, one X "
    "post (280 characters at most) and one LinkedIn post. The office's checks run at once and "
    "come back as errors (must fix) and warnings (worth a look).",
    {"type": "object",
     "properties": {"issue": {"type": "string", "description": "Id of the issue to revise (omit for a new one)."},
                    "title": {"type": "string"},
                    "body": {"type": "string", "description": "Markdown: ## headings, paragraphs, - bullets."},
                    "x_post": {"type": "string"}, "linkedin_post": {"type": "string"}},
     "required": ["title", "body", "x_post", "linkedin_post"]},
    _save_newsletter)


# read_newsletter -------------------------------------------------------------------------------
async def _read_newsletter(ctx: ToolContext, inp: dict) -> str:
    meta = _issue(ctx, inp)
    body = (outbox.issue_dir(ctx.office.outbox_dir, meta["id"]) / "draft.md").read_text()
    return json.dumps({**_report(meta), "title": meta["title"], "body": body, "x_post": meta["x_post"],
                       "linkedin_post": meta["linkedin_post"]}, indent=1)


READ_NEWSLETTER = Tool(
    "read_newsletter", "Read a newsletter draft from the Outbox: the text, both social posts and "
    "the current check results.",
    {"type": "object", "properties": {"issue": {"type": "string", "description": "The issue id."}},
     "required": ["issue"]}, _read_newsletter)


# finalize_newsletter ---------------------------------------------------------------------------
async def _finalize_newsletter(ctx: ToolContext, inp: dict) -> str:
    from hq.publish import get_publisher

    office = ctx.office
    meta = _issue(ctx, inp)
    note = _str(inp, "note", required=False, max_len=1500)
    if meta["status"] in outbox.LOCKED:
        raise GuardBlock(f"Issue {meta['id']} is {outbox.LOCKED[meta['status']]}.")
    problems = outbox.recheck(office, meta["id"])             # approvals may have changed since the save
    errors = [p for p in problems if p["level"] == "error"]
    if errors:
        outbox.set_status(office.outbox_dir, meta["id"], "blocked", problems=problems)
        raise GuardBlock("This issue can't go to " + office.captain_name + " yet:\n- "
                         + "\n- ".join(f"{p['where']}: {p['msg']}" for p in errors)
                         + "\nFix it with save_newsletter, then finalize again.")
    # Locked from here on, so the text that was just checked is the text that gets built.
    outbox.set_status(office.outbox_dir, meta["id"], "finalizing", problems=problems)
    try:
        publisher = get_publisher()
        meta = await asyncio.to_thread(publisher.prepare, office, meta["id"])
    except Exception as e:
        outbox.set_status(office.outbox_dir, meta["id"], "draft")
        raise GuardBlock(f"Building the files failed: {e}") from e
    warnings = [p for p in problems if p["level"] == "warning"]
    summary = (f"{meta['title']}: {meta['words']} words, with a header image, one X post and one "
               "LinkedIn post. Code checks passed: only approved numbers, no hype, no advice, "
               "disclaimer attached."
               + (" Worth a look: " + "; ".join(p["msg"] for p in warnings) if warnings else "")
               + (f"\n\n{note}" if note else "")
               + "\n\nNothing is posted anywhere. On approval the files are marked ready to paste.")
    approval_id = office.request_approval(
        ctx.agent.id, kind="newsletter", title=meta["title"], summary=summary,
        payload={"issue": meta["id"], "publisher": publisher.name,
                 "attachments": [f"/outbox/{path}" for path in meta["files"].values()]},
        task_id=ctx.task["id"])
    outbox.set_status(office.outbox_dir, meta["id"], "awaiting", approval_id=approval_id,
                      problems=problems, publisher=publisher.name)
    office.bus.publish("outbox_ready", ctx.agent.id, ctx.task["id"], issue=meta["id"],
                       title=meta["title"], approval=approval_id)
    return (f"Issue {meta['id']} is finalized and approval #{approval_id} is on "
            f"{office.captain_name}'s desk. The decision arrives as a message; wrap up.")


FINALIZE_NEWSLETTER = Tool(
    "finalize_newsletter",
    "Finish a newsletter issue: re-run the checks, build the ready-to-paste files (article, web "
    "page, header image, social posts, with the disclaimer) and put the issue on {captain}'s desk "
    "for approval. Refused while any check is failing. Nothing is posted anywhere.",
    {"type": "object",
     "properties": {"issue": {"type": "string", "description": "The issue id."},
                    "note": {"type": "string", "description": "Anything {captain} should know (optional)."}},
     "required": ["issue"]},
    _finalize_newsletter)


# package_memo ----------------------------------------------------------------------------------
async def _package_memo(ctx: ToolContext, inp: dict) -> str:
    from hq.tools.desk import _ticker

    office = ctx.office
    t = _ticker(inp)
    try:
        meta = await asyncio.to_thread(outbox.package_memo, office, t)
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    files = list(meta["files"].values())
    approval_id = office.request_approval(
        ctx.agent.id, kind="deliverable", title=f"{t} research memo for clients",
        summary=(f"The {t} memo packaged as a branded client file ({', '.join(meta['files'])}). "
                 f"Figures match Quant model v{meta['model_version']}, nothing is left to verify, "
                 "sources are on file and the disclaimer is attached. Nothing is sent to anyone."),
        payload={"ticker": t, "deliverable": meta["id"],
                 "attachments": [f"/outbox/{f}" for f in files]},
        task_id=ctx.task["id"])
    outbox.set_deliverable_status(office.outbox_dir, meta["id"], "awaiting", approval_id=approval_id)
    office.bus.publish("outbox_ready", ctx.agent.id, ctx.task["id"], deliverable=meta["id"],
                       title=meta["title"], approval=approval_id)
    return (f"{t} memo packaged ({', '.join(files)}) and approval #{approval_id} is on "
            f"{office.captain_name}'s desk.")


PACKAGE_MEMO = Tool(
    "package_memo",
    "Package a finished research memo (coverage memo.md) as a branded client file in the Outbox "
    "and put it on {captain}'s desk. Refused unless the ticker has an approved model, the memo's "
    "figures match it, no [VERIFY] is left and sources.md exists.",
    {"type": "object", "properties": {"ticker": {"type": "string", "description": "US ticker, e.g. RMBS"}},
     "required": ["ticker"]},
    _package_memo)


def client_tools(tier: str) -> list[Tool]:
    """Wren drafts and revises; Harbor also finalizes and packages."""
    tools = [NEWSLETTER_MATERIAL, SAVE_NEWSLETTER, READ_NEWSLETTER]
    if tier == "lead":
        tools += [FINALIZE_NEWSLETTER, PACKAGE_MEMO]
    return tools
