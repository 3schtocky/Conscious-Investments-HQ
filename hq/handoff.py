"""The hand-off to Client Relations: when a researched name is ready for a slidedeck.

Four things must be true for a ticker: the firm's approved Quant model rates it Outperform, Equity
Research has finished the initiating-coverage report, Quant has written the Model Brief for that
approved version, and the hand-off has not been made already for it. When they hold, code files a
"Ready for Client Relations" card on the Captain's desk. Nothing starts, and nothing is spent,
until he approves it; the approval hands Harbor the assignment. Plain code, no model calls.
"""

from __future__ import annotations

import logging

from hq import modelbrief
from hq.engine.runtime import CAPTAIN, OFFICE, Office
from hq.tools import desk

log = logging.getLogger(__name__)
KIND = "handoff"


def report_files(ticker: str) -> list[str]:
    """The finished initiating-coverage report (Word and/or PDF) in the ticker's coverage folder."""
    cdir = desk.coverage_dir(ticker)
    return sorted(p.name for p in cdir.glob("*Initiating-Coverage*") if p.suffix in (".docx", ".pdf")) if cdir.is_dir() else []


def _card(office: Office, ticker: str, version: int, status: tuple[str, ...]) -> dict | None:
    return next((c for c in office.store.approvals() if c["kind"] == KIND and c["status"] in status
                 and c["payload"].get("ticker") == ticker and c["payload"].get("version") == version), None)


def readiness(office: Office, ticker: str) -> dict:
    """Each condition with a plain reason, and whether the hand-off can be offered now."""
    model = office.store.approved_model(ticker)
    version = model["version"] if model else None
    rating = model["summary"].get("rating") if model else None
    brief = modelbrief.status(office, ticker)
    reports = report_files(ticker)
    conditions = [
        {"key": "model", "ok": model is not None,
         "detail": f"Approved Quant model v{version}." if model else
         f"{ticker} has no approved model. Quant builds one and the Captain approves it."},
        {"key": "rating", "ok": rating == "Outperform",
         "detail": "The approved model rates it Outperform." if rating == "Outperform" else
         (f"The approved model rates it {rating}; slidedecks are made for Outperform names." if model else
          "No approved model, so no rating.")},
        {"key": "report", "ok": bool(reports),
         "detail": f"Initiating-coverage report: {', '.join(reports)}." if reports else
         f"No finished initiating-coverage report in {ticker}'s coverage folder. Research builds it."},
        {"key": "brief", "ok": brief["state"] == "ready", "detail": brief["detail"]},
    ]
    done = _card(office, ticker, version, ("pending", "approved")) if version else None
    return {"ticker": ticker, "version": version, "ready": all(c["ok"] for c in conditions) and done is None,
            "conditions": conditions,
            "handoff": None if done is None else {"approval": done["id"], "status": done["status"]}}


def readiness_holds(office: Office, ticker: str, version: int) -> bool:
    """Do the conditions still hold for the version the card was filed for (a pending card ignored)?"""
    model = office.store.approved_model(ticker)
    if model is None or model["version"] != version or model["summary"].get("rating") != "Outperform":
        return False
    return bool(report_files(ticker)) and modelbrief.status(office, ticker)["state"] == "ready"


def _summary(office: Office, ticker: str, version: int) -> str:
    b = modelbrief.load(office, ticker, version)
    f, r = b["facts"], b["reading"]
    sc = f["scenarios"]
    return (f"{ticker} is ready for a slidedeck. Approved Quant model v{version} rates it "
            f"{f['rating']}: bear ${sc['bear']['price_target']:,.2f}, base ${sc['base']['price_target']:,.2f}, "
            f"bull ${sc['bull']['price_target']:,.2f} against a price of ${f['price']:,.2f} on {f['as_of']}. "
            f"The initiating-coverage report is finished and Quant's Model Brief is written.\n\n"
            f"Quant's view: {r['view']}\n\nApprove to start the slidedeck: Harbor will outline it from the "
            "report and the brief, and nothing is built or spent before you do. Decline to leave it.")


def maybe_offer(office: Office, ticker: str) -> int | None:
    """File the hand-off card if the ticker is ready and none exists. Never raises."""
    try:
        r = readiness(office, ticker)
        if not r["ready"]:
            return None
        card = office.request_approval(
            OFFICE, kind=KIND, title=f"Ready for Client Relations: {ticker}",
            summary=_summary(office, ticker, r["version"]), task_id=None,
            payload={"ticker": ticker, "version": r["version"], "attachments": [], "report": report_files(ticker)})
        return card
    except Exception:
        log.exception("hand-off offer failed for %s", ticker)
        return None


def decided(office: Office, card: dict, decision: str) -> int | None:
    """On approval, give Harbor the assignment. Returns the task id."""
    if card["kind"] != KIND or decision != "approved":
        return None
    p = card["payload"]
    ticker, version = p["ticker"], p["version"]
    lead = "cr_lead"
    if lead not in office.agents:
        return None
    me = office.agents[lead].nickname
    body = (f"{office.captain_name} approved the hand-off for {ticker} (Quant model v{version}, rated "
            f"Outperform). Prepare the slidedeck package.\n\n"
            f"Read first: get_model for the approved numbers, read_model_brief for Quant's brief, and "
            f"the initiating-coverage report in {ticker}'s coverage folder ({', '.join(p.get('report') or [])}; "
            "list_files shows it, read_file reads its sections). If anything is missing or unclear, ask "
            "the owning wing through its delegate with relay_request. Never work around a gap.\n\n"
            f"{me}: write the slide-by-slide outline (slidedeck_material lists the 17 slides), then delegate the copy to "
            "your associate with that outline: every slide's headline and, for the narrative slides, bullets that each name a "
            "source. Your associate saves it with save_slidedeck_copy and fixes every error. Read it back with read_slidedeck_copy, hold "
            "it to the release standard (claim check, risk parity, plain-English test, no-hype scan, register), then build_slidedeck. "
            "Have your associate draft the matching weekly note on this name with save_newsletter, then call finalize_slidedeck with "
            f"its issue id: that puts the slidedeck, source map, PDF and note on {office.captain_name}'s desk as one approval. "
            "Report in one line what the slidedeck says, what you changed and anything you could not verify.")
    task_id = office.store.create_task(assignee=lead, assigned_by=CAPTAIN, kind="assignment",
                                       title=f"Slidedeck: {ticker}"[:70], body=body)
    office._schedule(lead, task_id)
    return task_id


def scan(office: Office) -> list[int]:
    """Offer the hand-off for every name that has become ready. Cheap: reads the model registry."""
    out = []
    for t in sorted({m["ticker"] for m in office.store.models() if m["status"] == "approved"}):
        card = maybe_offer(office, t)
        if card:
            out.append(card)
    return out
