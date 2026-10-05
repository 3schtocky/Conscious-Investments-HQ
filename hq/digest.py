"""Activity digests: what each wing is doing right now, built by code from the event log.

Leads should spend their tokens building, not explaining. Everything the delegates and Juno need
to say about a lead's progress is read here from events the engine already records, so a status
answer costs the lead nothing and never goes stale. No model call, no costs (the ledger is the
Captain's), so it works while the office is paused.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hq.engine.runtime import Office

RECENT = 8   # activity lines kept per person
WINDOW = 400   # newest events scanned per person

# Tools that only look at things say "read"; tools that change something say what changed.
_VERBS = {
    "read_file": "read {path}", "list_files": "listed files", "search_file": "searched {path}",
    "write_file": "wrote {path}", "get_model": "looked up the approved model",
    "note_to_self": "wrote a desk note", "propose_wiki": "proposed a wiki change",
    "request_approval": "asked {captain} for a decision: {title}",
    "report_to_captain": "reported to {captain}", "submit_result": "handed in a result",
    "run_screen": "ran a screen", "read_screen": "read a screen",
    "pitch_memo": "wrote a pitch memo", "save_newsletter": "saved a newsletter draft",
    "finalize_newsletter": "finalized a newsletter",
}
_SKIP = {"read_office", "wing_status"}   # looking around is not progress


def _clip(text: Any, n: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _line(office: Office, ev: dict) -> str | None:
    """One plain sentence for an event, or None when it says nothing about progress."""
    kind, p = ev["type"], ev["payload"]
    if kind == "tool_call":
        tool = p.get("tool")
        if tool in _SKIP or tool is None:
            return None
        inp = p.get("input") if isinstance(p.get("input"), dict) else {}
        if tool == "delegate":
            return f"handed a job to {_nick(office, inp.get('to'))}: {_clip(inp.get('job'), 70)}"
        if tool == "send_message":
            return None   # the chat event carries it
        text = _VERBS.get(tool, "used " + str(tool).replace("_", " "))
        return text.format(path=inp.get("path") or inp.get("file") or "a file",
                           title=_clip(inp.get("title"), 60), captain=office.captain_name)
    if kind == "delegated":
        return None   # the delegate tool call already says it
    if kind == "task_started":
        return f"started: {_clip(p.get('title'), 70)}"
    if kind == "task_done":
        return "finished the task"
    if kind == "task_paused":
        return f"stopped ({p.get('reason')})"
    if kind == "guard_block":
        return f"was refused {p.get('tool')}: {_clip(p.get('reason'), 60)}"
    if kind == "tool_result" and p.get("is_error"):
        return f"hit a problem with {p.get('tool')}"
    if kind == "chat":
        who = ", ".join(_nick(office, r) for r in p.get("recipients", [])[:3])
        return f"messaged {who}: {_clip(p.get('text'), 70)}"
    return None


def _nick(office: Office, who: Any) -> str:
    try:
        return office.name(office.resolve(str(who)))
    except (KeyError, AttributeError):
        return str(who)


def person_digest(office: Office, agent_id: str) -> dict:
    """One colleague: status, the task in hand, how long and how many turns, recent steps."""
    agent = office.agents[agent_id]
    store = office.store
    task = store.task(agent.current_task) if agent.current_task else None
    events = store.events_where(agent=agent_id, limit=WINDOW)
    if task:   # only what this task did
        events = [e for e in events if e["task_id"] == task["id"]]
    lines = [(e["ts"], line) for e in events if (line := _line(office, e))]
    turns = sum(1 for e in events if e["type"] == "spend")
    return {
        "id": agent_id, "name": agent.nickname, "role": agent.role,
        "status": agent.status,
        "working_on": task["title"] if task else None,
        "task": task["id"] if task else None,
        "minutes_on_task": round((time.time() - task["created"]) / 60, 1) if task else None,
        "turns": turns if task else 0,
        "recent": [text for _, text in lines[-RECENT:]],
        "last_step_ts": lines[-1][0] if lines else None,
    }


def _sentence(office: Office, people: list[dict], blockers: list[str], waiting: list[str]) -> str:
    """The wing in one or two plain sentences, for a delegate or Juno to read out."""
    parts = []
    for p in people:
        if p["working_on"]:
            last = p["recent"][-1] if p["recent"] else "just started"
            parts.append(f"{p['name']} is on '{_clip(p['working_on'], 60)}' "
                         f"({p['turns']} turns, {p['minutes_on_task']} min); last: {last}")
        elif p["status"] in ("paused", "held"):
            parts.append(f"{p['name']} is {p['status']}")
    if not parts:
        parts.append("nobody here has work in hand")
    text = "; ".join(parts) + "."
    if blockers:
        text += " Blocked: " + "; ".join(blockers) + "."
    if waiting:
        text += f" Waiting on {office.captain_name}: " + "; ".join(waiting) + "."
    return text


def wing_digest(office: Office, wing: str) -> dict:
    """The state of one wing: its people, blockers, decisions waiting, and a one-line headline."""
    ids = [a.id for a in office.agents.values() if a.wing == wing]
    people = [person_digest(office, i) for i in ids]
    blockers: list[str] = []
    for inc in office.store.incidents():
        if inc["agent"] in ids:
            blockers.append(f"{_nick(office, inc['agent'])}: {_clip(inc['detail'], 80)}")
    for a in (office.agents[i] for i in ids):
        if a.paused and a.paused_by:
            blockers.append(f"{a.nickname} paused by {_nick(office, a.paused_by)}")
    for t in office.store.open_tasks():
        if t["assignee"] in ids and t["status"] in ("paused", "paused_budget", "error"):
            blockers.append(f"'{_clip(t['title'], 50)}' is {t['status']}"
                            + (f" ({t['status_reason']})" if t["status_reason"] else ""))
    waiting = [_clip(c["title"], 60) for c in office.store.approvals("pending") if c["agent"] in ids]
    queued = [t for t in office.store.open_tasks()
              if t["assignee"] in ids and t["status"] == "queued"]
    if blockers:
        state = "blocked"
    elif waiting and not any(p["working_on"] for p in people):
        state = "waiting"
    elif any(p["working_on"] for p in people):
        state = "working"
    elif queued:
        state = "queued"
    else:
        state = "idle"
    if office.held and state in ("working", "queued"):
        state = "held"   # work waits where it is until the Captain resumes the office
    return {"wing": wing, "name": office.wing_name(wing), "state": state, "people": people,
            "blockers": list(dict.fromkeys(blockers)), "waiting_on_captain": waiting,
            "queued": len(queued), "headline": _sentence(office, people, blockers, waiting)}


def office_digest(office: Office) -> dict:
    """Every wing, for Juno's rounds and the Rounds board. Same data live or paused."""
    wings = [wing_digest(office, w) for w in office._wings]
    busy = [w for w in wings if w["state"] != "idle"]
    return {"ts": time.time(), "held": office.held, "clocked_out": office.clocked_out,
            "active": bool(busy), "wings": wings}
