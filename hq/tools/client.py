"""Client Relations tools: the newsletter, client-ready memos and outreach emails.

Wren drafts; Harbor finalizes. Every draft passes the code gate in `hq.outbox.check_issue`
(only approved numbers, no hype, no advice) before it can reach the Captain, and nothing leaves
the Outbox without his approval. Nothing here calls the Anthropic API or posts anywhere.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from hq import outbox, outreach
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


# ---- outreach: one-to-one emails from contact@ -----------------------------------------------
def _email(ctx: ToolContext, inp: dict) -> dict:
    try:
        return outreach.load(ctx.office, _str(inp, "email", max_len=60))
    except KeyError as e:
        raise GuardBlock(str(e.args[0])) from e


def _email_report(meta: dict) -> dict:
    errors = [p for p in meta["problems"] if p["level"] == "error"]
    warnings = [p for p in meta["problems"] if p["level"] == "warning"]
    return {"email": meta["id"], "status": meta["status"], "words": meta["words"],
            "errors": [f"{p['where']}: {p['msg']}" for p in errors],
            "warnings": [f"{p['where']}: {p['msg']}" for p in warnings],
            "next": ("Fix every error and save again." if errors
                     else "Clean. The Client Relations lead can finalize it with finalize_outreach.")}


async def _outreach_context(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    cfg = outreach.settings()
    pub = outbox.publishable(office)
    to = _str(inp, "to_email", required=False, max_len=200)
    past = []
    if to:
        past = [{"email": m["id"], "date": m["date"], "subject": m["subject"], "status": m["status"]}
                for m in outreach.history(office, to)]
    researched = [{"ticker": t, "rating": m["rating"], "price_targets": m["targets"],
                   "cite_as": f"Quant model v{m['version']}"} for t, m in pub["approved"].items()]
    return json.dumps({
        "what_we_offer": cfg["product"],
        "links_allowed": cfg["domains"],
        "researched_names": researched,
        "contact_history": past if to else "Pass to_email to see whether we have written to this person.",
        "suppressed": bool(to and outreach.is_suppressed(office, to)),
        "rules": [
            "One person per email. The Captain names the recipient; never invent or guess an address.",
            "Say who we are, what we offer, and why this person. About 80 to 200 words, plain text.",
            "Offer: the weekly note and the live office. One clear, low-pressure next step.",
            ("Targets and ratings only for researched_names, exactly as given, ticker in the same "
             "sentence. Usually leave numbers out of a first email."),
            "No hype, no advice, no promises, no performance claims beyond what the scoreboard shows.",
            "Links only to our own site. The opt-out line, postal address and disclaimer are added for you.",
            f"No repeat email to one person within {cfg['min_gap_days']} days; at most {cfg['max_touches']} in all."]},
        indent=1)


OUTREACH_CONTEXT = Tool(
    "outreach_context",
    "What an outreach email may say right now: what we offer, the researched names with approved "
    "numbers, the allowed links, the rules, and (with to_email) whether we have already written to "
    "that person or must not. Start every outreach email here.",
    {"type": "object", "properties": {"to_email": {"type": "string"}}, "required": []},
    _outreach_context)


async def _save_outreach(ctx: ToolContext, inp: dict) -> str:
    try:
        meta = outreach.save_draft(
            ctx.office, agent_id=ctx.agent.id, to_name=_str(inp, "to_name", required=False, max_len=120),
            to_email=_str(inp, "to_email", max_len=200), org=_str(inp, "org", required=False, max_len=120),
            segment=_str(inp, "segment", max_len=20), reason=_str(inp, "reason", required=False, max_len=400),
            subject=_str(inp, "subject", max_len=300), body=_str(inp, "body", max_len=6000),
            email_id=_str(inp, "email", required=False, max_len=60) or None)
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    except KeyError as e:
        raise GuardBlock(str(e.args[0])) from e
    ctx.office.bus.publish("outreach_draft", ctx.agent.id, ctx.task["id"], email=meta["id"],
                           status=meta["status"], words=meta["words"])
    return json.dumps(_email_report(meta), indent=1)


SAVE_OUTREACH = Tool(
    "save_outreach",
    "Save an outreach email draft to the Outbox. To revise one, pass its id as `email`. The code "
    "checks run at once and come back as errors (must fix) and warnings (worth a look). Give "
    "`reason` as a clause that completes \"You are receiving this because ...\" (it appears in the "
    "footer and on {captain}'s card). Do not write a sign-off address, an opt-out line or a "
    "disclaimer; they are added for you.",
    {"type": "object",
     "properties": {"email": {"type": "string", "description": "Id of the draft to revise (omit for a new one)."},
                    "to_name": {"type": "string"}, "to_email": {"type": "string"},
                    "org": {"type": "string", "description": "Their organisation, if any."},
                    "segment": {"type": "string", "enum": list(outreach.SEGMENTS),
                                "description": "business = a firm or professional; retail = an individual investor; "
                                               "inbound = someone who asked to hear from us."},
                    "reason": {"type": "string", "description": "e.g. \"you asked to hear from us\" or \"we thought our "
                                                                 "research may be useful to your readers\""},
                    "subject": {"type": "string"}, "body": {"type": "string", "description": "Plain text paragraphs."}},
     "required": ["to_email", "segment", "reason", "subject", "body"]},
    _save_outreach)


async def _read_outreach(ctx: ToolContext, inp: dict) -> str:
    meta = _email(ctx, inp)
    return json.dumps({**_email_report(meta), "to": f"{meta['to_name']} <{meta['to_email']}>",
                       "org": meta["org"], "segment": meta["segment"], "reason": meta["reason"],
                       "subject": meta["subject"], "body": meta["body"]}, indent=1)


READ_OUTREACH = Tool(
    "read_outreach", "Read an outreach email draft back, with its current check results.",
    {"type": "object", "properties": {"email": {"type": "string", "description": "The email id."}},
     "required": ["email"]}, _read_outreach)


async def _finalize_outreach(ctx: ToolContext, inp: dict) -> str:
    from hq.publish import get_mailer

    office = ctx.office
    meta = _email(ctx, inp)
    note = _str(inp, "note", required=False, max_len=1500)
    if meta["status"] in outreach.LOCKED:
        raise GuardBlock(f"Email {meta['id']} is {outreach.LOCKED[meta['status']]}.")
    problems = outreach.recheck(office, meta["id"])
    errors = [p for p in problems if p["level"] == "error"]
    if errors:
        outreach.set_status(office, meta["id"], "blocked", problems=problems)
        raise GuardBlock("This email can't go to " + office.captain_name + " yet:\n- "
                         + "\n- ".join(f"{p['where']}: {p['msg']}" for p in errors)
                         + "\nFix it with save_outreach, then finalize again.")
    outreach.set_status(office, meta["id"], "finalizing", problems=problems)
    try:
        mailer = get_mailer()
        meta = await asyncio.to_thread(mailer.prepare, office, meta["id"])
    except Exception as e:
        outreach.set_status(office, meta["id"], "draft")
        raise GuardBlock(f"Building the email failed: {e}") from e
    warnings = [p for p in problems if p["level"] == "warning"]
    who = meta["to_name"] or meta["to_email"]
    summary = (f"To {who}" + (f" at {meta['org']}" if meta["org"] else "") + f" <{meta['to_email']}> "
               f"({outreach.SEGMENTS[meta['segment']]}). Why: {meta['reason']}.\n"
               f"Subject: {meta['subject']}\n{meta['words']} words. Code checks passed: valid address, not "
               "suppressed, within the contact limits, our own links only, no hype or advice, approved "
               "numbers only; footer with our address, opt-out and disclaimer attached."
               + (" Worth a look: " + "; ".join(p["msg"] for p in warnings) if warnings else "")
               + (f"\n\n{note}" if note else "")
               + f"\n\nNothing is sent until you approve. {mailer.on_approval}")
    approval_id = office.request_approval(
        ctx.agent.id, kind="outreach", title=f"Email to {who}: {meta['subject']}"[:120], summary=summary,
        payload={"email": meta["id"], "mailer": mailer.name,
                 "attachments": [f"/outbox/{path}" for path in meta["files"].values()]},
        task_id=ctx.task["id"])
    outreach.set_status(office, meta["id"], "awaiting", approval_id=approval_id, problems=problems,
                        mailer=mailer.name)
    office.bus.publish("outreach_ready", ctx.agent.id, ctx.task["id"], email=meta["id"], approval=approval_id)
    return (f"Email {meta['id']} is finalized and approval #{approval_id} is on "
            f"{office.captain_name}'s desk. The decision arrives as a message; wrap up.")


FINALIZE_OUTREACH = Tool(
    "finalize_outreach",
    "Finish an outreach email: re-run every check, build the final text with the footer (our "
    "address, opt-out, disclaimer) and put it on {captain}'s desk for approval. Refused while any "
    "check is failing. Nothing is sent until {captain} approves.",
    {"type": "object",
     "properties": {"email": {"type": "string", "description": "The email id."},
                    "note": {"type": "string", "description": "Anything {captain} should know (optional)."}},
     "required": ["email"]},
    _finalize_outreach)


async def _suppress_contact(ctx: ToolContext, inp: dict) -> str:
    try:
        row = outreach.suppress(ctx.office, _str(inp, "address", max_len=200), _str(inp, "reason", max_len=300))
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    return f"{row['address']} is on the do-not-contact list. No email to it can pass the checks."


SUPPRESS_CONTACT = Tool(
    "suppress_contact",
    "Put an address (or a whole @domain) on the do-not-contact list: someone asked not to hear "
    "from us, or {captain} says to stop. Do this at once when you hear it.",
    {"type": "object",
     "properties": {"address": {"type": "string", "description": "name@example.com or @example.com"},
                    "reason": {"type": "string"}},
     "required": ["address", "reason"]},
    _suppress_contact)


def client_tools(tier: str) -> list[Tool]:
    """Wren drafts and revises; Harbor also finalizes and packages."""
    tools = [NEWSLETTER_MATERIAL, SAVE_NEWSLETTER, READ_NEWSLETTER,
             OUTREACH_CONTEXT, SAVE_OUTREACH, READ_OUTREACH]
    if tier == "lead":
        tools += [FINALIZE_NEWSLETTER, PACKAGE_MEMO, FINALIZE_OUTREACH, SUPPRESS_CONTACT]
    return tools
