"""Audit tools: how Vera and Tally read the office's records and act on what they find.

Reading (both): `audit_log`, `read_spend`, `read_findings`, plus the file and model readers
from the desk tools. Acting (Vera only): `resolve_finding`, `file_incident`, `pause_agent`.

Audit can pause one colleague at a time and must say why; the Captain is alerted and only the
Captain unpauses. Nothing here calls the Anthropic API.
"""

from __future__ import annotations

import json
import time

from HQ.engine.guards import GuardBlock
from HQ.tools.office import Tool, ToolContext, _str

LOG_TYPES = ["task_started", "task_done", "task_paused", "task_error", "tool_call", "tool_result",
             "guard_block", "chat", "captain_report", "delegated", "file_written",
             "approval_requested", "approval_decided", "incident", "text", "model_built",
             "watchlist_added", "memory_saved", "memory_held"]
INCIDENT_KINDS = ("ethics", "mission_drift", "sourcing", "spend", "loop", "code_health", "other")
MAX_LOG = 80


def _short(val, n: int = 220) -> str:
    s = val if isinstance(val, str) else json.dumps(val, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _log_line(office, e: dict) -> str | None:
    p = e["payload"]
    who = office.name(e["agent"]) if e["agent"] in office.agents else (e["agent"] or "office")
    stamp = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
    task = f" #{e['task_id']}" if e["task_id"] else ""
    match e["type"]:
        case "task_started":
            body = f"started \"{p.get('title')}\" ({p.get('kind')}, from {p.get('assigned_by')})"
        case "task_done":
            body = "finished"
        case "task_paused":
            body = f"PAUSED ({p.get('reason')}): {_short(p.get('detail') or '')}"
        case "task_error":
            body = f"ERROR: {_short(p.get('error') or '')}"
        case "tool_call":
            body = f"tool {p.get('tool')}({_short(p.get('input'), 160)})"
        case "tool_result":
            if not p.get("is_error"):
                return None   # successful results are noise here; the call line is enough
            body = f"tool {p.get('tool')} FAILED: {_short(p.get('text') or '', 160)}"
        case "guard_block":
            body = f"guard refused {p.get('tool')}: {_short(p.get('reason') or '', 160)}"
        case "chat":
            to = ", ".join(office.name(r) if r in office.agents else r
                           for r in p.get("recipients", []))
            body = f"to {to}: {_short(p.get('text') or '')}"
        case "captain_report":
            body = f"to {office.captain_name}: {_short(p.get('text') or '')}"
        case "delegated":
            body = f"delegated to {office.name(p['to']) if p.get('to') in office.agents else p.get('to')}: " \
                   f"{_short(p.get('job') or '', 160)}"
        case "file_written":
            body = f"wrote {p.get('ticker')} {p.get('path')} ({p.get('chars')} chars)"
        case "approval_requested":
            body = f"asked for approval #{p.get('approval')} ({p.get('kind')}): {p.get('title')}"
        case "approval_decided":
            body = f"approval #{p.get('approval')} {p.get('decision')}"
        case "incident":
            body = f"INCIDENT {p.get('kind')}: {_short(p.get('detail') or '')}"
        case "text":
            body = f"said: {_short(p.get('text') or '')}"
        case "model_built":
            body = f"built {p.get('ticker')} model v{p.get('version')} (check ok: {p.get('ok')})"
        case "watchlist_added":
            body = f"added {p.get('ticker')} to the watchlist ({p.get('source')})"
        case "memory_saved":
            body = f"saved a desk note: {_short(p.get('text') or '', 160)}"
        case "memory_held":
            body = f"memory write held for review: {_short(p.get('text') or '', 160)}"
        case _:
            return None
    return f"{stamp} {who}{task}: {body}"


# audit_log ------------------------------------------------------------------------------------
async def _audit_log(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    agent = inp.get("agent")
    agent_id = office.resolve(agent) if isinstance(agent, str) and agent.strip() else None
    task_id = inp.get("task_id")
    if task_id is not None and (not isinstance(task_id, int) or isinstance(task_id, bool)):
        raise GuardBlock("`task_id` must be a whole number.")
    if agent_id is None and task_id is None:
        raise GuardBlock("Give an `agent`, a `task_id`, or both: the whole log is too long to read.")
    limit = inp.get("limit") or 40
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise GuardBlock("`limit` must be a whole number.")
    limit = min(max(limit, 1), MAX_LOG)
    task_ids = None
    if task_id is not None:   # a task and the jobs delegated from it
        try:
            office.store.task(task_id)
        except KeyError as e:
            raise GuardBlock(f"No task #{task_id}.") from e
        task_ids = [task_id] + [c["id"] for c in office.store.children(task_id)]
    rows = office.store.events_where(agent=agent_id, task_ids=task_ids, types=LOG_TYPES,
                                     limit=limit * 3)
    lines = [ln for ln in (_log_line(office, e) for e in rows) if ln][-limit:]
    return "\n".join(lines) or "Nothing logged for that yet."


AUDIT_LOG = Tool(
    "audit_log",
    "Read the office's activity log for one colleague, one task (with the jobs delegated from "
    "it), or both: tasks, tool calls and failures, guard refusals, messages, files written, "
    "approvals and incidents, oldest first. Use it for evidence before you rule on anything.",
    {"type": "object",
     "properties": {"agent": {"type": "string", "description": "Colleague id or nickname."},
                    "task_id": {"type": "integer"},
                    "limit": {"type": "integer", "description": f"Lines to return (max {MAX_LOG})."}},
     "required": []},
    _audit_log)


# read_spend -----------------------------------------------------------------------------------
async def _read_spend(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    d = office.digest()
    cfg_cap = office.ledger.daily_cap
    return json.dumps({
        "day": d["day"], "spent_today_usd": round(d["spend"]["total"], 2), "daily_cap_usd": cfg_cap,
        "by_colleague": [{"name": r["name"], "usd": round(r["cost"], 2), "calls": r["calls"]}
                         for r in d["spend"]["by_agent"]],
        "tasks": d["tasks"], "tool_calls_failed_or_refused": d["tool_errors"],
    }, indent=1)


READ_SPEND = Tool(
    "read_spend",
    "Today's spend from the office ledger: the total against the daily cap, spend and call "
    "counts by colleague, task counts and failed tool calls.",
    {"type": "object", "properties": {}, "required": []}, _read_spend)


# read_findings --------------------------------------------------------------------------------
async def _read_findings(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    rows = office.store.findings(statuses=("open", "reviewing"), limit=60)
    if inp.get("include_closed"):
        rows = office.store.findings(limit=60)
    return json.dumps([{"finding": f["id"], "rule": f["rule"], "severity": f["severity"],
                        "colleague": office.name(f["agent"]) if f["agent"] in office.agents else f["agent"],
                        "task_id": f["task_id"], "subject": f["subject"], "detail": f["detail"],
                        "status": f["status"], "note": f["note"]} for f in rows], indent=1)


READ_FINDINGS = Tool(
    "read_findings",
    "The findings from Tally's code checks that are still open (flags you review, notes for the "
    "digest). Pass include_closed=true to see recent rulings too.",
    {"type": "object", "properties": {"include_closed": {"type": "boolean"}}, "required": []},
    _read_findings)


# resolve_finding ------------------------------------------------------------------------------
async def _resolve_finding(ctx: ToolContext, inp: dict) -> str:
    finding_id = inp.get("finding")
    if not isinstance(finding_id, int) or isinstance(finding_id, bool):
        raise GuardBlock("`finding` must be the finding's number.")
    verdict = inp.get("verdict")
    if verdict not in ("cleared", "upheld"):
        raise GuardBlock("`verdict` must be cleared or upheld.")
    note = _str(inp, "note", max_len=600)
    try:
        ctx.office.resolve_finding(finding_id, verdict, by=ctx.agent.id, note=note)
    except KeyError as e:
        raise GuardBlock(f"No finding #{finding_id}. Use read_findings.") from e
    except ValueError as e:
        raise GuardBlock(str(e)) from e
    if verdict == "upheld":
        return (f"Finding #{finding_id} upheld. If you haven't yet, tell the colleague exactly "
                "what to fix with send_message.")
    return f"Finding #{finding_id} cleared."


RESOLVE_FINDING = Tool(
    "resolve_finding",
    "Close a finding after you have looked at the evidence: cleared (the check was wrong or it "
    "is fine in context) or upheld (it stands and needs fixing). The note is one line on why; "
    "{captain} reads it.",
    {"type": "object",
     "properties": {"finding": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["cleared", "upheld"]},
                    "note": {"type": "string", "description": "One line: the evidence and why."}},
     "required": ["finding", "verdict", "note"]},
    _resolve_finding)


# file_incident --------------------------------------------------------------------------------
async def _file_incident(ctx: ToolContext, inp: dict) -> str:
    kind = inp.get("kind")
    if kind not in INCIDENT_KINDS:
        raise GuardBlock(f"`kind` must be one of: {', '.join(INCIDENT_KINDS)}.")
    detail = _str(inp, "detail", max_len=1200)
    who = inp.get("agent")
    agent_id = ctx.office.resolve(who) if isinstance(who, str) and who.strip() else None
    incident_id = ctx.office.raise_incident(agent_id, ctx.task["id"], f"audit: {kind}", detail,
                                            by=ctx.agent.id)
    return f"Incident #{incident_id} is on {ctx.office.captain_name}'s desk."


FILE_INCIDENT = Tool(
    "file_incident",
    "Put an incident card on {captain}'s desk for something he should know about but that does "
    "not need anyone paused: an ethics or mission concern, weak sourcing, wasteful spend, a "
    "loop, a tooling problem. Say what you saw, where (task, file), and what you recommend.",
    {"type": "object",
     "properties": {"kind": {"type": "string", "enum": list(INCIDENT_KINDS)},
                    "agent": {"type": "string", "description": "The colleague concerned, if any."},
                    "detail": {"type": "string"}},
     "required": ["kind", "detail"]},
    _file_incident)


# pause_agent ----------------------------------------------------------------------------------
async def _pause_agent(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    target = office.agents[office.resolve(_str(inp, "agent", max_len=100))]
    reason = _str(inp, "reason", max_len=600)
    if len(reason) < 20:
        raise GuardBlock("Give the reason in a full sentence: what you saw and where.")
    if target.id == ctx.agent.id:
        raise GuardBlock("You can't pause yourself.")
    if target.paused:
        raise GuardBlock(f"{target.nickname} is already paused.")
    held = [a for a in office.agents.values()
            if a.paused and a.paused_by in office.agents and office.agents[a.paused_by].is_audit]
    if held:
        raise GuardBlock(f"{held[0].nickname} is already paused on Audit's call, and Audit pauses "
                         f"one colleague at a time. {office.captain_name} has to unpause them "
                         "first; file an incident for this one instead.")
    office.pause(target.id, by=ctx.agent.id, reason=reason)
    await office.report_to_captain(
        ctx.agent.id, f"I paused {target.nickname}: {reason} Only you can unpause them.",
        task_id=ctx.task["id"])
    return (f"{target.nickname} is paused and {office.captain_name} has been alerted. Only "
            f"{office.captain_name} can unpause. Don't message {target.nickname} about it.")


PAUSE_AGENT = Tool(
    "pause_agent",
    "Stop one colleague from taking another step until {captain} unpauses them. A last resort "
    "for something serious or something that keeps happening after you asked for a fix: "
    "invented figures, unapproved numbers heading to a client, work outside the mission, a "
    "runaway loop. {captain} is alerted with your reason. You can hold one colleague at a time.",
    {"type": "object",
     "properties": {"agent": {"type": "string", "description": "Colleague id or nickname."},
                    "reason": {"type": "string",
                               "description": "What you saw and where (task, file, figure)."}},
     "required": ["agent", "reason"]},
    _pause_agent)


def audit_tools(tier: str) -> list[Tool]:
    """Audit's desk tools. Reading for both; rulings, incidents and the pause for the lead."""
    tools = [AUDIT_LOG, READ_SPEND, READ_FINDINGS]
    if tier == "lead":
        tools += [RESOLVE_FINDING, FILE_INCIDENT, PAUSE_AGENT]
    return tools
