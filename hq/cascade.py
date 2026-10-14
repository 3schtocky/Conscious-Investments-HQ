"""Model cascade: the wing's associate (Haiku) takes an assignment first; the lead (Sonnet) only
steps in when the first pass trips a code trigger, and polishes the draft instead of redoing it.

Triggers are plain code: failed or refused tool calls (the gates), a Tally flag, a guard trip
(turns, $ cap, context, loop), an error or a refusal. The associate's own `confidence` is
recorded but never decides. Off unless `cascade.enabled` and a kind matches (config/office.yaml).
Also the read-only reports behind `hq spend --by-tier`.
"""

from __future__ import annotations

import json
import logging
import re

from hq.config import model_config, office

log = logging.getLogger(__name__)
DEFAULTS = {"gate_fails": 2, "attempt_cap_usd": 0.40, "max_turns": 20, "lead_review": False}
GUARD_KINDS = {"turn_cap", "task_cost_cap", "context_cap", "tool_loop"}
LEAD_REVIEW = "lead_review"   # a clean pass the lead still finishes: not counted as an escalation


# configuration -------------------------------------------------------------------------------
def kind_for(agent_id: str, title: str, body: str) -> tuple[str, dict] | None:
    """The cascade kind (name, settings) an assignment to `agent_id` falls under, if any."""
    cfg = office().get("cascade") or {}
    if not cfg.get("enabled"):
        return None
    for name, k in (cfg.get("kinds") or {}).items():
        if k.get("lead") != agent_id:
            continue
        try:
            hit = not k.get("match") or re.search(k["match"], f"{title}\n{body}", re.IGNORECASE)
        except re.error as e:   # a typo in the config must not block every assignment to this lead
            log.warning("cascade kind %r has a bad match pattern: %s", name, e)
            continue
        if hit:
            return name, settings(name)
    return None


def settings(name: str | None) -> dict:
    """A kind's settings over the defaults; a kind removed from the config mid-flight still resolves."""
    cfg = office().get("cascade") or {}
    return {**DEFAULTS, **(cfg.get("defaults") or {}), **((cfg.get("kinds") or {}).get(name) or {})}


def record(errors: list[tuple[str, str]], task: dict, tool: str, text: str, is_error: bool) -> None:
    """Note a tool result against a first pass. A raised refusal or failure counts; so does a check that
    fails in plain words (Quant's "Formula check FAILED") when the kind lists that wording in
    `fail_markers`. With `gate_tools` only those tools count. A later pass of the same check clears its
    earlier plain-words failures: a fixed workbook is not a failure."""
    k = settings(task.get("cascade"))
    if k.get("gate_tools") and tool not in k["gate_tools"]:
        return
    markers = k.get("fail_markers") or []
    failed = is_error or any(m in text for m in markers)
    if failed:
        errors.append((tool, text))
    elif markers:
        errors[:] = [e for e in errors if not (e[0] == tool and any(m in e[1] for m in markers))]


def is_first_pass(task: dict) -> bool:
    return bool(task.get("cascade")) and task.get("attempt") == 1


# triggers -------------------------------------------------------------------------------------
def review(office_, task: dict, outcome: dict, errors: list[tuple[str, str]]) -> str | None:
    """Why this first pass needs the lead, or None to accept it. `errors` are its failed tool calls."""
    k = settings(task["cascade"])
    status = outcome.get("status")
    if status in ("error", "declined"):
        return status
    if status == "paused":
        return outcome["reason"] if outcome.get("reason") in GUARD_KINDS else None   # budget / API off: just wait
    if status != "done":
        return None
    if len(errors) >= k["gate_fails"]:
        return "gate_fails"
    if office_.store.task_flags(task["id"]):
        return "audit_flag"
    return LEAD_REVIEW if k.get("lead_review") else None


def draft_text(result) -> str:
    """The first pass's work product as the lead (or the Captain) should read it."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return result
    if not isinstance(result, dict):
        return str(result or "")
    parts = [str(result.get("findings") or "").strip()]
    figs = result.get("figures") or []
    if figs:
        parts.append("Figures:\n" + "\n".join(
            f"- {f.get('label')}: {f.get('value')}{(' ' + f['unit']) if f.get('unit') else ''}"
            f" ({f.get('period') or 'n/a'}; {f.get('source')})" for f in figs))
    if result.get("open_questions"):
        parts.append("Open questions:\n" + "\n".join(f"- {q}" for q in result["open_questions"]))
    if result.get("notes"):
        parts.append(str(result["notes"]))
    return "\n\n".join(p for p in parts if p)


def polish_body(task: dict, reason: str, outcome: dict, errors: list[tuple[str, str]]) -> str:
    """The lead's brief: the original request, the draft, and exactly what stopped the first pass."""
    stuck = {"gate_fails": "its tool calls were refused or failed too often", "audit_flag": "Tally flagged its work",
             "turn_cap": "it ran out of turns", "task_cost_cap": "it reached its first-pass $ cap",
             "context_cap": "its conversation grew too large", "tool_loop": "it repeated the same call",
             "error": "it hit an error", "declined": "the model declined",
             LEAD_REVIEW: "this kind of work is always finished by the lead"}[reason]
    draft = draft_text(outcome.get("result")) if outcome.get("status") == "done" else ""
    out = [f"Polish pass. Your wing's associate took the first pass at this and handed it up because {stuck}.",
           ("Fix only what is wrong: keep what is sound, do not start over, check every figure against its source,"
            " then finish the assignment as you normally would (report, approval, sign-off)."),
           f"Original assignment: **{task['title']}**\n\n{task['body']}"]
    if draft:
        out.append(f"The first pass's draft:\n\n{draft}")
    elif outcome.get("detail"):
        out.append(f"Where it stopped: {outcome['detail']}")
    if errors:
        out.append("What was refused or failed (latest first):\n" + "\n".join(
            f"- {name}: {text[:300]}" for name, text in reversed(errors[-5:])))
    out.append("The first pass's files and notes are in the wing's desk and folders: read them with your tools.")
    return "\n\n".join(out)


def escalate(office_, task: dict, reason: str, outcome: dict, errors: list[tuple[str, str]]) -> int:
    """Close the first pass and hand its draft to the lead as a fresh task (never a mid-conversation
    model swap: Sonnet's thinking blocks and the cache prefix belong to one model)."""
    store = office_.store
    # The wing's lead from the roster, not the config: always valid, even if the kind was edited meanwhile.
    lead = office_.lead_of(office_.agents[task["assignee"]].wing).id
    new_id = store.create_task(   # the successor exists before the first pass is closed: work is never orphaned
        assignee=lead, assigned_by=task["assigned_by"], kind="assignment", title=task["title"],
        body=polish_body(task, reason, outcome, errors), parent_id=task["id"], root_id=task["root_id"],
        attempt=2, escalated_from=task["id"], escalation_reason=reason, cascade=task["cascade"])
    store.set_task_status(task["id"], "done", reason=f"escalated: {reason}")
    office_.bus.publish("task_created", lead, new_id, title=task["title"], kind="assignment",
                        assigned_by=task["assigned_by"])
    office_.bus.publish("cascade_escalated", task["assignee"], task["id"], to=lead, reason=reason, task=new_id)
    office_._schedule(lead, new_id)
    return new_id


async def after_first_pass(office_, task: dict, outcome: dict, errors: list[tuple[str, str]]) -> dict:
    """Called when a first pass ends: hand it up, or accept it and tell the Captain."""
    reason = review(office_, task, outcome, errors)
    if reason:
        escalate(office_, task, reason, outcome, errors)
        return {**outcome, "escalated": reason}
    if outcome.get("status") == "done" and task["assigned_by"] == "captain" and task["id"] not in office_.reported:
        await office_.report_to_captain(task["assignee"], draft_text(outcome.get("result")), task_id=task["id"])
    return outcome


# reports (read-only) --------------------------------------------------------------------------
def tier_of(model: str) -> str:
    return model_config(model)[0]


def by_tier(store, day: str | None = None) -> dict:
    """Spend per tier, and per assignment: which tiers touched it and what it cost in each."""
    tiers: dict[str, dict] = {}
    roots: dict[int | None, dict] = {}
    for r in store.spend_by_root_model(day):
        tier = tier_of(r["model"])
        t = tiers.setdefault(tier, {"calls": 0, "cost": 0.0})
        t["calls"] += r["calls"]
        t["cost"] += r["cost"]
        roots.setdefault(r["root_id"], {})[tier] = roots.get(r["root_id"], {}).get(tier, 0.0) + r["cost"]
    return {"tiers": tiers, "roots": roots}


def summary(store, day: str | None = None) -> dict:
    """Cascade health: assignments that started on the cheap tier, how many the lead had to take
    over (a polish task points back at its first pass), and why. `lead_review` hand-ups are
    designed in, so they are counted apart from escalations."""
    all_tasks = tasks = store.tasks()
    if day:   # tasks created that local day, matching the ledger's `day` column
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(office()["budget"]["timezone"])
        tasks = [t for t in tasks if datetime.fromtimestamp(t["created"], tz).strftime("%Y-%m-%d") == day]
    started = [t for t in tasks if t["cascade"] and t["attempt"] == 1]
    handed = {t["escalated_from"]: t["escalation_reason"] for t in all_tasks if t["attempt"] == 2 and t["escalated_from"]}
    reasons: dict[str, int] = {}
    for r in handed.values():
        reasons[r] = reasons.get(r, 0) + 1
    escalated = [t for t in started if handed.get(t["id"]) not in (None, LEAD_REVIEW)]
    n = len(started)
    return {"haiku_first": n, "lead_reviewed": sum(1 for t in started if handed.get(t["id"]) == LEAD_REVIEW),
            "finished_on_haiku": sum(1 for t in started if t["id"] not in handed and t["status"] == "done"),
            "escalated": len(escalated), "escalation_rate": (len(escalated) / n) if n else 0.0, "reasons": reasons}
