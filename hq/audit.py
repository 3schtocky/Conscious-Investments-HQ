"""Audit's code checks: free, deterministic, and run on every piece of work.

These are Tally's checks. They cost nothing (no model call), so they run on every deliverable
and every finished assignment:

- `check_document`: sourcing and valuation rules on a brief, memo or pitch.
- `check_approval`: the same rules on a card before it reaches the Captain's desk.
- `check_task`: tool failures and spend on a finished assignment.
- `screen_note`: what may be saved to office memory without the Captain looking first.
- `build_digest` / `render_digest`: the daily audit digest, compiled from the ledger and logs.

A finding is a **flag** (Vera reviews it) or a **note** (it only appears in the digest). The
checks are deliberately simple: they point at things worth a look, and Vera or the Captain
decides. Code-level guards in `hq.engine.guards` are separate and stop work on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hq.config import office as office_config

if TYPE_CHECKING:
    from hq.engine.runtime import Office

DELIVERABLES = {"brief.md": "brief", "memo.md": "memo", "pitch.md": "pitch"}
TOOL_ERROR_NOTE = 4        # this many failed or refused tool calls in one assignment is worth a note
TASK_SPEND_NOTE = 0.6      # share of the per-task cap that is worth a note

_PT_WORDS = r"(?:(?i:price\s+targets?|target\s+price)|\bPT\b)"
_USD = r"\$\s?(\d[\d,]*(?:\.\d+)?)"
_PT_AFTER = re.compile(_PT_WORDS + r"[^$\n]{0,40}?" + _USD)
_PT_BEFORE = re.compile(_USD + r"\s+" + _PT_WORDS)
_STREET = re.compile(r"street|consensus|analyst|sell-side|\bmean\b|\bmedian\b|\baverage\b", re.IGNORECASE)
_RATING = re.compile(
    r"\b(?:we\s+rate|rating\s*(?::|of|is)|rated|initiat\w+\s+(?:at|with))\W+(?:\w+\W+){0,3}?"
    r"(buy|sell|hold|outperform|underperform|overweight|underweight|neutral)\b", re.IGNORECASE)
_VERIFY = re.compile(r"\[VERIFY")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str    # flag | note
    subject: str
    detail: str


def _usd(n: float) -> str:
    return f"${n:,.2f}"


def price_targets(text: str) -> list[float]:
    """Price targets a text states as its own (lines quoting the Street are left alone)."""
    out: list[float] = []
    for line in text.splitlines():
        if _STREET.search(line):
            continue
        for rx in (_PT_AFTER, _PT_BEFORE):
            for m in rx.finditer(line):
                try:
                    out.append(float(m.group(1).replace(",", "")))
                except ValueError:
                    continue
    return out


def _targets(model: dict) -> list[float]:
    return [float(v) for v in model["summary"].get("price_targets", {}).values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _approved_targets(office: Office, ticker: str | None) -> dict | None:
    """The approved price targets a text may quote. With no ticker (a card covering several
    names) any approved model's target is acceptable."""
    if not ticker:
        models = [m for m in office.store.models() if m["status"] == "approved"]
        if not models:
            return None
        return {"version": None, "targets": [t for m in models for t in _targets(m)]}
    m = office.store.approved_model(ticker)
    if m is None:
        return None
    return {"version": m["version"], "targets": _targets(m)}


def _figure_findings(text: str, subject: str, ticker: str | None,
                     approved: dict | None) -> list[Finding]:
    """Price targets must come from the approved model (mission rule 2)."""
    found = price_targets(text)
    if not found:
        return []
    name = ticker or "this name"
    if approved is None:
        return [Finding("unapproved_figures", "flag", subject,
                        f"States a price target ({', '.join(_usd(n) for n in found[:4])}) but "
                        f"{name} has no approved model.")]
    off = [n for n in found
           if not any(abs(n - t) <= max(0.01, 0.005 * abs(t)) for t in approved["targets"])]
    if not off:
        return []
    if approved["version"] is None:
        return [Finding("unapproved_figures", "flag", subject,
                        f"Price target {', '.join(_usd(n) for n in off[:4])} does not match any "
                        "approved model.")]
    return [Finding("unapproved_figures", "flag", subject,
                    f"Price target {', '.join(_usd(n) for n in off[:4])} does not match approved "
                    f"model v{approved['version']} "
                    f"({' / '.join(_usd(t) for t in approved['targets'])}).")]


def check_document(kind: str, text: str, *, subject: str, ticker: str | None = None,
                   approved: dict | None = None, has_sources: bool = True,
                   quant: bool = False) -> list[Finding]:
    """Rules for one deliverable. `kind` is brief, memo or pitch."""
    out: list[Finding] = []
    left = len(_VERIFY.findall(text))
    if left:
        final = kind == "memo"
        out.append(Finding("verify_left", "flag" if final else "note", subject,
                           f"{left} [VERIFY] marker{'s' if left != 1 else ''} still in the "
                           + ("finished memo." if final else f"{kind}.")))
    if kind == "pitch":
        targets = price_targets(text)
        rated = [m.group(1) for line in text.splitlines() if not _STREET.search(line)
                 for m in _RATING.finditer(line)]
        if targets or rated:
            what = []
            if targets:
                what.append(f"a price target ({', '.join(_usd(n) for n in targets[:3])})")
            if rated:
                what.append(f"a rating ({rated[0]})")
            out.append(Finding("valuation_in_pitch", "flag", subject,
                               f"A pitch describes the setup, not the valuation, but this one "
                               f"states {' and '.join(what)}."))
    elif not quant:
        out += _figure_findings(text, subject, ticker, approved)
    if kind in ("brief", "memo") and not has_sources:
        out.append(Finding("missing_sources", "flag" if kind == "memo" else "note", subject,
                           f"The {kind} was written but there is no sources.md beside it."))
    return out


def _check_file(office: Office, ticker: str, rel: str, *, quant: bool) -> list[Finding]:
    from hq.tools import desk

    kind = DELIVERABLES.get(Path(rel).name)
    if kind is None or "/" in rel or not desk.TICKER.match(ticker):
        return []   # only a ticker's own top-level deliverables; never a path built from free text
    root = desk.coverage_dir(ticker).resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return []
    return check_document(kind, path.read_text(errors="replace"), subject=f"{ticker} {rel}",
                          ticker=ticker, approved=_approved_targets(office, ticker),
                          has_sources=(path.parent / "sources.md").is_file(), quant=quant)


def check_approval(office: Office, agent_id: str, *, kind: str, title: str, summary: str,
                   payload: dict) -> list[Finding]:
    """Checks on an approval card before the Captain reads it. Quant's own cards (a model
    version, a conflict) carry Quant's numbers by design and are not checked for figures."""
    agent = office.agents.get(agent_id)
    quant = bool(agent and agent.wing == "quant")
    ticker = payload.get("ticker")
    out: list[Finding] = []
    if not quant and kind not in ("model", "conflict"):
        out += _figure_findings(summary, f'approval card "{title}"', ticker,
                                _approved_targets(office, ticker))
    if ticker:
        for rel in payload.get("attachments", []):
            if isinstance(rel, str):
                out += _check_file(office, ticker, rel.lstrip("/"), quant=quant)
    return out


def check_task(office: Office, task: dict) -> list[Finding]:
    """Checks on a finished assignment: the files it wrote, failed tool calls and spend.
    Delegated jobs are covered through the lead's assignment."""
    store = office.store
    agent = office.agents.get(task["assignee"])
    quant = bool(agent and agent.wing == "quant")
    ids = [task["id"]] + [c["id"] for c in store.children(task["id"], "delegation")]
    events = store.events_where(task_ids=ids, types=["file_written", "tool_result"], limit=3000)
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for e in events:
        if e["type"] != "file_written":
            continue
        key = (str(e["payload"].get("ticker")), str(e["payload"].get("path")))
        if key in seen:
            continue
        seen.add(key)
        out += _check_file(office, key[0], key[1], quant=quant)
    subject = f"task #{task['id']}: {task['title']}"
    errors = sum(1 for e in events if e["type"] == "tool_result" and e["payload"].get("is_error"))
    if errors >= TOOL_ERROR_NOTE:
        out.append(Finding("tool_errors", "note", subject,
                           f"{errors} tool calls failed or were refused during this assignment."))
    cap = office_config()["budget"]["per_task_cap_usd"]
    spent = sum(store.spend_for_task(i) for i in ids)
    if cap and spent >= TASK_SPEND_NOTE * cap:
        out.append(Finding("task_spend", "note", subject,
                           f"Used {_usd(spent)} of the {_usd(cap)} cap for one piece of work."))
    return out


# ---- memory screen ---------------------------------------------------------------------------
_NOTE_FIGURES = re.compile(r"\$\s?\d|\d(?:\.\d+)?\s?%|\b\d+(?:\.\d+)?x\b")
_NOTE_VALUATION = re.compile(
    r"price\s+target|target\s+price|\bPT\b|\b(?:buy|sell|outperform|underperform|overweight|"
    r"underweight)\s+rating|\brated\s+(?:a\s+)?(?:buy|sell|hold|outperform|underperform)", re.IGNORECASE)
_RULES = (r"(?:audit|vera|tally|approv\w*|guard\w*|budget|cap|limit\w*|rule\w*|check\w*|review\w*|"
          r"lint|instruction\w*|polic\w*|sourc\w*)")
_NOTE_BYPASS = re.compile(
    r"\b(?:ignore|bypass|override|disable|circumvent|skip|avoid|work\s+around|get\s+around)\b"
    r"[^.\n]{0,40}?\b" + _RULES + r"\b"
    r"|\b(?:don'?t|do\s+not|never)\s+(?:tell|mention|report|flag)\b"
    r"|\bkeep\s+(?:this|it)\s+from\b|\bwithout\s+(?:approval|asking|telling)\b"
    r"|\balways\s+approve\b|api\.enabled|\bunpause\b", re.IGNORECASE)
_NOTE_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]{8,}|api[ _\-]?key|passw(?:or)?d|\bsecret\b|"
                          r"\b(?:access|auth|bearer)\s+token\b", re.IGNORECASE)


def screen_note(text: str) -> list[str]:
    """Why a memory write should wait for the Captain; an empty list means it is clean."""
    reasons = []
    if _NOTE_VALUATION.search(text):
        reasons.append("states a valuation or rating (those come from the approved model)")
    elif _NOTE_FIGURES.search(text):
        reasons.append("contains figures (they belong in the model or the brief)")
    if _NOTE_BYPASS.search(text):
        reasons.append("reads like a way around a rule or a review")
    if _NOTE_SECRET.search(text):
        reasons.append("may contain a credential")
    return reasons


# ---- daily digest ----------------------------------------------------------------------------
def day_bounds(day: str, tz) -> tuple[float, float]:
    d = date.fromisoformat(day)
    nxt = d + timedelta(days=1)
    return (datetime(d.year, d.month, d.day, tzinfo=tz).timestamp(),
            datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).timestamp())


def build_digest(office: Office, day: str) -> dict:
    """The numbers behind one day's audit digest, straight from the ledger and the logs."""
    store = office.store
    start, end = day_bounds(day, office.ledger.tz)
    name = lambda a: office.name(a) if a in office.agents or a == "captain" else (a or "Office")

    by_agent: dict[str, dict] = {}
    for row in store.spend_breakdown(day):
        slot = by_agent.setdefault(row["agent"], {"agent": row["agent"], "name": name(row["agent"]),
                                                  "cost": 0.0, "calls": 0})
        slot["cost"] += row["cost"]
        slot["calls"] += row["calls"]
    spend = {"total": store.spend_for_day(day), "cap": office.ledger.daily_cap,
             "by_agent": sorted(by_agent.values(), key=lambda r: (-r["cost"], r["name"]))}

    updated = store.tasks_between(start, end, by="updated")
    count = lambda *statuses: sum(1 for t in updated if t["status"] in statuses)
    tasks = {"started": len(store.tasks_between(start, end)), "done": count("done"),
             "paused": count("paused", "paused_budget"), "error": count("error"),
             "declined": count("declined")}

    incidents = [{"id": i["id"], "kind": i["kind"], "agent": i["agent"], "name": name(i["agent"]),
                  "detail": i["detail"], "resolved": bool(i["resolved"])}
                 for i in store.incidents_between(start, end)]

    found = store.findings(start=start, end=end, limit=1000)
    flags = [f for f in found if f["severity"] == "flag"]
    findings = {
        "flags": len(flags), "notes": len(found) - len(flags),
        "open": sum(1 for f in flags if f["status"] in ("open", "reviewing")),
        "cleared": sum(1 for f in flags if f["status"] == "cleared"),
        "upheld": sum(1 for f in flags if f["status"] == "upheld"),
        "dismissed": sum(1 for f in flags if f["status"] == "dismissed"),
        "items": [{"id": f["id"], "rule": f["rule"], "severity": f["severity"],
                   "name": name(f["agent"]), "subject": f["subject"], "detail": f["detail"],
                   "status": f["status"]} for f in found],
    }

    tool_errors = store.count_tool_errors(start, end)

    writes = store.memory_writes(start=start, end=end, limit=1000)
    mcount = lambda kind, *st: sum(1 for w in writes if w["kind"] == kind and w["status"] in st)
    memory = {"saved": mcount("desk", "saved", "approved"), "held": mcount("desk", "pending"),
              "rejected": mcount("desk", "rejected"), "removed": mcount("desk", "removed"),
              "wiki_proposed": sum(1 for w in writes if w["kind"] == "wiki"),
              "wiki_approved": mcount("wiki", "approved")}

    cards = store.approvals()
    decided = [a for a in cards if a["decided_ts"] and start <= a["decided_ts"] < end]
    approvals = {"requested": sum(1 for a in cards if start <= a["ts"] < end),
                 "approved": sum(1 for a in decided if a["status"] == "approved"),
                 "changes": sum(1 for a in decided if a["status"] == "changes"),
                 "rejected": sum(1 for a in decided if a["status"] == "rejected")}

    # What still needs the Captain, as of now.
    open_items = []
    paused = [p for p in store.pauses() if p["agent"] in office.agents]
    for p in paused:
        open_items.append(f"{name(p['agent'])} is paused ({p['reason']}). Only you can unpause.")
    open_flags = store.count_findings("open", "flag")
    if open_flags:
        open_items.append(f"{open_flags} flag{'s' if open_flags != 1 else ''} not yet reviewed.")
    held = store.count_memory("pending")
    if held:
        open_items.append(f"{held} memory write{'s' if held != 1 else ''} held for your approval.")
    pending = len(store.approvals("pending"))
    if pending:
        open_items.append(f"{pending} approval card{'s' if pending != 1 else ''} on your desk.")
    unack = len(store.incidents())
    if unack:
        open_items.append(f"{unack} incident{'s' if unack != 1 else ''} not acknowledged.")
    stuck = [t for t in store.tasks("paused") + store.tasks("error")]
    if stuck:
        open_items.append(f"{len(stuck)} task{'s' if len(stuck) != 1 else ''} paused or failed: "
                          + ", ".join(f"#{t['id']}" for t in stuck[:8]) + ".")

    return {"day": day, "spend": spend, "tasks": tasks, "incidents": incidents,
            "findings": findings, "tool_errors": tool_errors, "memory": memory,
            "approvals": approvals, "open_items": open_items,
            "active": bool(tasks["started"] or spend["total"] > 0 or found or incidents)}


def render_digest(d: dict) -> str:
    """The digest as plain text for the Captain's chat. Costs to the cent."""
    s, t, f, m, a = d["spend"], d["tasks"], d["findings"], d["memory"], d["approvals"]
    plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
    lines = [f"Audit digest for {d['day']} (compiled by code, no API cost)."]
    top = ", ".join(f"{r['name']} {_usd(r['cost'])}" for r in s["by_agent"][:4])
    rest = sum(r["cost"] for r in s["by_agent"][4:])
    if rest:
        top += f", others {_usd(rest)}"
    lines.append(f"Spend: {_usd(s['total'])} of {_usd(s['cap'])}" + (f" ({top})." if top else "."))
    lines.append(f"Work: {plural(t['started'], 'task')} started, {t['done']} finished, "
                 f"{t['paused']} paused, {t['error']} failed"
                 + (f", {t['declined']} declined." if t["declined"] else "."))
    checks = f"Checks: {plural(f['flags'], 'flag')}"
    if f["flags"]:
        parts = [f"{f[k]} {label}" for k, label in (("open", "open"), ("upheld", "upheld"),
                                                    ("cleared", "cleared"),
                                                    ("dismissed", "dismissed")) if f[k]]
        checks += f" ({', '.join(parts)})"
    lines.append(f"{checks}, {plural(f['notes'], 'note')}. "
                 f"{plural(d['tool_errors'], 'tool call')} failed or refused.")
    if d["incidents"]:
        lines.append("Incidents: " + "; ".join(f"{i['kind']} ({i['name']})"
                                               for i in d["incidents"][:6]) + ".")
    else:
        lines.append("Incidents: none.")
    mem = f"Memory: {plural(m['saved'], 'note')} saved, {m['held']} held for you"
    if m["rejected"] or m["removed"]:
        mem += f", {m['rejected'] + m['removed']} rejected or removed"
    if m["wiki_proposed"]:
        mem += f"; {plural(m['wiki_proposed'], 'wiki proposal')} ({m['wiki_approved']} approved)"
    lines.append(mem + ".")
    lines.append(f"Approvals: {a['requested']} requested, {a['approved']} approved, "
                 f"{a['changes']} sent back, {a['rejected']} declined.")
    if d["open_items"]:
        lines.append("Needs you:")
        lines += [f"- {item}" for item in d["open_items"]]
    else:
        lines.append("Nothing needs you.")
    return "\n".join(lines)
