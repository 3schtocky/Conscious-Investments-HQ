"""Client outreach: one-to-one emails from contact@consciousinvestments.org about our product.

Client Relations' second function (the first is the newsletter). On the Captain's request Wren
drafts and Harbor finalizes an email to one named person; the Captain approves it; nothing leaves
before that. Each email is a folder:

    outbox/outreach/<date>-<slug>/
        meta.json    recipient, why, status, check results, approval card, history
        email.txt    the final text: To / From / Subject, the body and the code-built footer
        email.html   the same, to open and read
    outbox/outreach/suppressed.json   addresses and domains we never write to again

`check_email` is the code gate (free). The footer (who we are, our postal address, how to opt out,
the disclaimer) is added by code and never written by an agent. Everything here is gitignored.
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hq import outbox
from hq.config import office as office_config

if TYPE_CHECKING:
    from hq.engine.runtime import Office

OUTREACH_ID = outbox.ISSUE_ID
SEGMENTS = {"business": "Business or professional", "retail": "Individual investor",
            "inbound": "Asked to hear from us"}
WORDS_LOW, WORDS_HIGH = 40, 260
SUBJECT_LIMIT, REASON_LIMIT = 90, 160
SPENT = ("approved", "sent")                 # statuses that count as a touch
LOCKED = {"awaiting": "waiting for a decision", "approved": "approved", "sent": "already sent",
          "finalizing": "being finalized right now"}

DEFAULT_DISCLAIMER = (
    "Conscious Investments publishes independent equity research for information and education "
    "only. Nothing here is investment advice or an offer to buy or sell any security. Portions of "
    "our work are drafted with AI tools and reviewed by the author.")
EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@([A-Za-z0-9\-]+\.)+[A-Za-z]{2,}$")
URL = re.compile(r"https?://[^\s)>\]]+|\bwww\.[^\s)>\]]+", re.IGNORECASE)
_PERFORMANCE = re.compile(r"(?i)\b(beat(?:ing|s)? the (?:market|s&p(?: 500)?)|track record|our returns?|"
                          r"market[- ]beating|outperform(?:ed|ing|s)? the (?:market|s&p(?: 500)?))\b")


_PITCHY = re.compile(r"(?i)\b(game[- ]chang\w+|massive|explosive|skyrocket\w*|soaring|revolutionary|"
                    r"don'?t\s+miss(?:\s+out)?|act\s+now|limited[- ]time|exclusive\s+(?:offer|access)|"
                    r"unbeatable|incredible\s+opportunity|last\s+chance)\b")


def settings() -> dict:
    cfg = (office_config().get("client_relations", {}) or {}).get("outreach", {}) or {}
    return {"from_address": cfg.get("from_address", "contact@consciousinvestments.org"),
            "from_name": cfg.get("from_name", "Conscious Investments"),
            "postal_address": " ".join((cfg.get("postal_address") or "").split()),
            "disclaimer": " ".join((cfg.get("disclaimer") or DEFAULT_DISCLAIMER).split()),
            "domains": [d.lower() for d in cfg.get("link_domains", ["consciousinvestments.org"])],
            "min_gap_days": int(cfg.get("min_gap_days", 14)),
            "max_touches": int(cfg.get("max_touches", 3)),
            "mailer": cfg.get("mailer", "draft"),
            "product": " ".join((cfg.get("product") or (
                "A free weekly research note with the price targets and ratings we have actually "
                "approved, and a live view of the AI-run research office at consciousinvestments.org "
                "where visitors can watch the work happen.")).split())}


# ---- on disk ----------------------------------------------------------------------------------
def root(office: Office) -> Path:
    return office.outbox_dir / "outreach"


def folder(office: Office, email_id: str) -> Path:
    if not OUTREACH_ID.match(email_id):
        raise KeyError(f"No outreach email {email_id!r}. Use the id save_outreach returned.")
    return root(office) / email_id


def load(office: Office, email_id: str) -> dict:
    path = folder(office, email_id) / "meta.json"
    if not path.is_file():
        raise KeyError(f"No outreach email {email_id!r}. Use the id save_outreach returned.")
    return json.loads(path.read_text())


def _save_meta(office: Office, meta: dict) -> dict:
    meta["updated"] = time.time()
    (folder(office, meta["id"]) / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def set_status(office: Office, email_id: str, status: str, **extra) -> dict:
    meta = load(office, email_id)
    meta.update(status=status, **extra)
    return _save_meta(office, meta)


def emails(office: Office) -> list[dict]:
    """Every outreach email, newest first."""
    out = []
    for path in root(office).glob("*/meta.json"):
        try:
            out.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return sorted(out, key=lambda m: m.get("updated", 0), reverse=True)


# ---- who we must not write to -------------------------------------------------------------------
def _suppressed_path(office: Office) -> Path:
    return root(office) / "suppressed.json"


def suppressed(office: Office) -> list[dict]:
    try:
        return json.loads(_suppressed_path(office).read_text())
    except (OSError, ValueError):
        return []


def suppress(office: Office, address: str, reason: str) -> dict:
    """Never write to this address (or `@domain`) again."""
    key = address.strip().lower()
    if not (EMAIL.match(key) or re.match(r"^@([a-z0-9\-]+\.)+[a-z]{2,}$", key)):
        raise ValueError(f"{address!r} is not an email address or an @domain.")
    rows = [r for r in suppressed(office) if r["address"] != key]
    row = {"address": key, "reason": reason.strip()[:300], "at": time.time()}
    rows.append(row)
    root(office).mkdir(parents=True, exist_ok=True)
    _suppressed_path(office).write_text(json.dumps(rows, indent=1))
    return row


def is_suppressed(office: Office, address: str) -> dict | None:
    key = address.strip().lower()
    domain = "@" + key.split("@")[-1]
    for row in suppressed(office):
        if row["address"] in (key, domain):
            return row
    return None


def history(office: Office, address: str, exclude: str | None = None) -> list[dict]:
    """Earlier emails to this address that were approved or sent, oldest first."""
    key = address.strip().lower()
    rows = [m for m in emails(office) if m["to_email"].lower() == key and m["id"] != exclude
            and m["status"] in SPENT]
    return sorted(rows, key=lambda m: m.get("approved_at") or m.get("updated", 0))


# ---- the code gate ----------------------------------------------------------------------------
def _p(level: str, code: str, where: str, msg: str) -> dict:
    return {"level": level, "code": code, "where": where, "msg": msg}


def _words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9$%'’.@]+", text))


def check_email(office: Office, *, to_email: str, segment: str, reason: str, subject: str, body: str,
                email_id: str | None = None) -> list[dict]:
    """Every reason this email may not go out (errors) and things worth a second look (warnings)."""
    cfg = settings()
    out: list[dict] = []
    to = to_email.strip()
    if not EMAIL.match(to):
        out.append(_p("error", "address", "to", f"{to_email!r} is not a valid email address."))
    elif to.lower() == cfg["from_address"].lower():
        out.append(_p("error", "address", "to", "That is our own address."))
    if segment not in SEGMENTS:
        out.append(_p("error", "segment", "to", f"Segment must be one of: {', '.join(SEGMENTS)}."))
    if not reason.strip():
        out.append(_p("error", "reason", "reason",
                      "Say why this person is being contacted. It is shown to the Captain and in the footer."))
    if EMAIL.match(to):
        hit = is_suppressed(office, to)
        if hit:
            out.append(_p("error", "suppressed", "to",
                          f"We must not write to {to} ({hit['reason'] or 'asked not to be contacted'})."))
        past = history(office, to, exclude=email_id)
        if len(past) >= cfg["max_touches"]:
            out.append(_p("error", "touches", "to",
                          f"Already {len(past)} emails to {to}; the limit is {cfg['max_touches']}."))
        elif past:
            last = datetime.fromtimestamp(past[-1].get("approved_at") or past[-1]["updated"], office.ledger.tz).date()
            wait = last + timedelta(days=cfg["min_gap_days"])
            if date.fromisoformat(office.ledger.today()) < wait:
                out.append(_p("error", "gap", "to",
                              f"We wrote to {to} on {last.isoformat()}; wait until {wait.isoformat()} "
                              f"({cfg['min_gap_days']} days between emails)."))
    # subject
    s = subject.strip()
    if not s:
        out.append(_p("error", "subject", "subject", "The subject is missing."))
    else:
        if len(s) > SUBJECT_LIMIT:
            out.append(_p("error", "subject", "subject", f"{len(s)} characters; keep it under {SUBJECT_LIMIT}."))
        if re.match(r"(?i)^\s*(re|fwd?)\s*:", s):
            out.append(_p("error", "deceptive", "subject", "No fake Re: or Fwd: prefix. The subject must be honest."))
        if "!" in s or any(w.isupper() and len(w) > 3 for w in re.findall(r"[A-Za-z]+", s)):
            out.append(_p("error", "spammy", "subject", "No exclamation marks or ALL CAPS words in a subject."))
    # body
    pub = outbox.publishable(office)
    out += outbox._check_text("subject", subject, pub)
    out += outbox._check_text("email", body, pub)
    n = _words(body)
    if n < WORDS_LOW or n > WORDS_HIGH:
        out.append(_p("error" if n > WORDS_HIGH + 100 else "warning", "length", "email",
                      f"{n} words; a first email runs about 80 to 200."))
    for m in URL.finditer(body + " " + subject):
        host = re.sub(r"^https?://", "", m.group(0), flags=re.IGNORECASE).split("/")[0].lower().removeprefix("www.")
        if not any(host == d or host.endswith("." + d) for d in cfg["domains"]):
            out.append(_p("error", "link", "email", f"Link to {host} is not allowed; only {', '.join(cfg['domains'])}."))
    for m in _PITCHY.finditer(subject + " " + body):
        out.append(_p("error", "hype", "email", f"Sales language: \"{m.group(0)}\". Outreach stays measured."))
    if re.search(r"(?i)\bunsubscribe\b|postal address", body):
        out.append(_p("warning", "footer", "email", "The opt-out line and our address are added for you; don't write them."))
    for m in _PERFORMANCE.finditer(body):
        out.append(_p("warning", "performance", "email",
                      f"Performance claim (\"{m.group(0)}\"). Only say what the scoreboard shows, with the period."))
    if segment == "retail":
        out.append(_p("warning", "retail", "to",
                      "An individual investor: confirm you may contact this person (consent, or a lawful "
                      "basis where they live). The Captain sees this on the card."))
    return out


def sender_problems() -> list[dict]:
    """What must be configured before any email can leave."""
    cfg = settings()
    if not cfg["postal_address"]:
        return [_p("error", "postal-address", "setup",
                   "No postal address is set (client_relations.outreach.postal_address in office.yaml). "
                   "Commercial email must carry a valid physical address.")]
    return []


# ---- draft, finalize ----------------------------------------------------------------------------
def slug(name: str, org: str) -> str:
    return outbox.slugify(f"{org or name}")


def save_draft(office: Office, *, agent_id: str, to_name: str, to_email: str, org: str, segment: str,
               reason: str, subject: str, body: str, email_id: str | None = None,
               day: str | None = None) -> dict:
    """Save a draft and run the code checks. With `email_id` this revises that email; otherwise
    a new one (a second email to the same person on the same day gets its own number)."""
    if email_id:
        old = load(office, email_id)
    else:
        day = day or office.ledger.today()
        # Saving again for the same person on the same day revises that email, not a second one.
        same = [m for m in emails(office) if m["to_email"].lower() == to_email.strip().lower()
                and m["date"] == day and m["status"] not in (*SPENT, "rejected", "expired")]
        if same:
            old = same[0]
            email_id = old["id"]
        else:
            base = f"{day}-{slug(to_name, org)}"
            email_id, old, n = base, None, 1
            while (folder(office, email_id) / "meta.json").is_file():
                n += 1
                email_id = f"{base[:48]}-{n}"
    if old and old["status"] in LOCKED:
        raise ValueError(f"Email {email_id} is {LOCKED[old['status']]}; it can't be changed. Draft a new one.")
    folder(office, email_id).mkdir(parents=True, exist_ok=True)
    problems = check_email(office, to_email=to_email, segment=segment, reason=reason, subject=subject,
                           body=body, email_id=email_id)
    errors = [p for p in problems if p["level"] == "error"]
    old = old or {}
    meta = {"id": email_id, "to_name": to_name.strip(), "to_email": to_email.strip(), "org": org.strip(),
            "segment": segment, "reason": reason.strip(), "subject": subject.strip(),
            "body": body.strip(), "words": _words(body), "date": old.get("date") or email_id[:10],
            "status": "blocked" if errors else "draft", "problems": problems,
            "drafted_by": old.get("drafted_by", agent_id), "revised_by": agent_id,
            "created": old.get("created", time.time()), "revisions": old.get("revisions", 0) + 1,
            "files": {}, "approval_id": None}
    return _save_meta(office, meta)


def recheck(office: Office, email_id: str) -> list[dict]:
    """The checks again, against what is approved and who we have written to right now."""
    m = load(office, email_id)
    return sender_problems() + check_email(office, to_email=m["to_email"], segment=m["segment"],
                                           reason=m["reason"], subject=m["subject"], body=m["body"],
                                           email_id=email_id)


def footer(meta: dict) -> str:
    cfg = settings()
    why = meta["reason"].strip().rstrip(".")
    return "\n".join([
        f"{cfg['from_name']} | {cfg['from_address']}", cfg["postal_address"],
        f"You are receiving this because {why}.",
        'If you would rather not hear from us, reply "unsubscribe" and we will not write again.',
        "", cfg["disclaimer"]])


def full_text(meta: dict) -> str:
    cfg = settings()
    to = f"{meta['to_name']} <{meta['to_email']}>" if meta["to_name"] else meta["to_email"]
    return (f"To: {to}\nFrom: {cfg['from_name']} <{cfg['from_address']}>\nSubject: {meta['subject']}\n\n"
            f"{meta['body'].strip()}\n\n--\n{footer(meta)}\n")


def _html(meta: dict) -> str:
    def paras(text: str) -> str:
        return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", text.strip()))
    cfg = settings()
    return ("<!doctype html><meta charset=utf-8><title>" + html.escape(meta["subject"]) + "</title>"
            "<body style='font:16px/1.55 Georgia,serif;max-width:620px;margin:2rem auto;padding:0 1rem;color:#222'>"
            f"<p style='font:13px sans-serif;color:#666'>To: {html.escape(meta['to_email'])}<br>"
            f"From: {html.escape(cfg['from_name'])} &lt;{html.escape(cfg['from_address'])}&gt;<br>"
            f"Subject: <b>{html.escape(meta['subject'])}</b></p><hr>" + paras(meta["body"])
            + "<hr style='margin-top:2rem'><div style='font:12px/1.5 sans-serif;color:#666'>"
            + paras(footer(meta)) + "</div></body>")


def render(office: Office, email_id: str) -> dict:
    meta = load(office, email_id)
    d = folder(office, email_id)
    (d / "email.txt").write_text(full_text(meta))
    (d / "email.html").write_text(_html(meta))
    meta["files"] = {"email.txt": f"outreach/{email_id}/email.txt", "email.html": f"outreach/{email_id}/email.html"}
    return _save_meta(office, meta)
