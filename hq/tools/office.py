"""Office tools: how agents talk to each other, hand off work and report to the Captain.

Each tool is a JSON-schema definition plus an async handler `(ctx, input) -> str`. Handlers
validate their own input (model output is untrusted) and raise `GuardBlock` to refuse an action
with a reason the agent can act on.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hq.engine.guards import GuardBlock

if TYPE_CHECKING:
    from hq.engine.agent import Agent
    from hq.engine.runtime import Office


@dataclass
class ToolContext:
    office: Office
    agent: Agent
    task: dict
    finished: dict | None = None   # set by submit_result: ends the task after this turn


Handler = Callable[[ToolContext, dict], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Handler

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


def _str(inp: dict, key: str, *, required: bool = True, max_len: int = 20000) -> str:
    val = inp.get(key)
    if val is None and not required:
        return ""
    if not isinstance(val, str) or not val.strip():
        raise GuardBlock(f"`{key}` must be a non-empty string.")
    if len(val) > max_len:
        raise GuardBlock(f"`{key}` is too long ({len(val)} chars, max {max_len}).")
    return val.strip()


# send_message -----------------------------------------------------------------------------
async def _send_message(ctx: ToolContext, inp: dict) -> str:
    to = inp.get("to")
    if isinstance(to, str):
        to = [to]
    if not isinstance(to, list) or not to or not all(isinstance(t, str) for t in to):
        raise GuardBlock("`to` must be a list of teammate ids or nicknames.")
    text = _str(inp, "text", max_len=4000)
    recipients = [ctx.office.resolve(t) for t in to]
    if ctx.agent.id in recipients:
        raise GuardBlock("You can't message yourself.")
    await ctx.office.send_message(ctx.agent.id, recipients, text, task_id=ctx.task["id"])
    names = ", ".join(ctx.office.agents[r].nickname for r in recipients)
    return f"Delivered to {names}. Replies arrive as new messages; don't wait by re-sending."


SEND_MESSAGE = Tool(
    name="send_message",
    description=(
        "Send a short chat message to one or more teammates (by id or nickname). Use it to "
        "coordinate, ask a quick question, or share a finding. Replies arrive later as messages "
        "in your conversation. Keep messages brief and specific."),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"},
                   "description": "Teammate ids or nicknames, e.g. [\"Ledger\"]"},
            "text": {"type": "string", "description": "The message."},
        },
        "required": ["to", "text"],
    },
    handler=_send_message,
)


# delegate ---------------------------------------------------------------------------------
async def _delegate(ctx: ToolContext, inp: dict) -> str:
    to = ctx.office.resolve(_str(inp, "to", max_len=100))
    job = _str(inp, "job", max_len=8000)
    target = ctx.office.agents[to]
    if target.tier != "associate":
        raise GuardBlock(f"{target.nickname} is not an associate; delegate only to associates.")
    if target.wing != ctx.agent.wing:
        raise GuardBlock(f"{target.nickname} works in another wing. Delegate to your own "
                         "associate, or message the other wing's lead.")
    ctx.agent.guard.on_delegate()
    result = await ctx.office.delegate(ctx.agent.id, to, job, parent_task=ctx.task)
    return json.dumps(result, indent=1)


DELEGATE = Tool(
    name="delegate",
    description=(
        "Hand a narrow, well-defined job to your associate (runs on a faster, cheaper model in its "
        "own workspace). Use it for data pulls, reading long documents, tool runs, fact-finding "
        "and first drafts, so your own context stays small. Say exactly what to return. You get "
        "back a compact JSON summary: findings, figures with sources, open questions, confidence. "
        "Spot-check important figures before relying on them."),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Associate id or nickname."},
            "job": {"type": "string",
                    "description": "The job: goal, scope, what to return, and what not to do."},
        },
        "required": ["to", "job"],
    },
    handler=_delegate,
)


# submit_result ----------------------------------------------------------------------------
_CONFIDENCE = {"high", "medium", "low"}


async def _submit_result(ctx: ToolContext, inp: dict) -> str:
    findings = _str(inp, "findings", max_len=8000)
    figures = inp.get("figures") or []
    if not isinstance(figures, list):
        raise GuardBlock("`figures` must be a list.")
    clean_figures = []
    for f in figures[:50]:
        if not isinstance(f, dict) or not isinstance(f.get("value"), (str, int, float)):
            raise GuardBlock("Each figure needs at least a `value` (and ideally unit, period, "
                             "source).")
        clean_figures.append({k: f.get(k) for k in ("label", "value", "unit", "period", "source")
                              if f.get(k) is not None})
    open_q = inp.get("open_questions") or []
    if not isinstance(open_q, list) or not all(isinstance(q, str) for q in open_q):
        raise GuardBlock("`open_questions` must be a list of strings.")
    confidence = inp.get("confidence")
    if confidence not in _CONFIDENCE:
        raise GuardBlock("`confidence` must be high, medium or low.")
    ctx.finished = {"findings": findings, "figures": clean_figures,
                    "open_questions": open_q[:20], "confidence": confidence}
    return "Result submitted. Your lead has it; you're done with this job."


SUBMIT_RESULT = Tool(
    name="submit_result",
    description=(
        "Finish a delegated job by returning your result to the lead who assigned it. Call this "
        "once, as your last action. The lead sees only this result, so `findings` must contain "
        "the deliverable itself (the list, table or draft), not a summary of it. Every figure needs a source (filing, model output or URL); "
        "if you could not source one, include it with source \"[VERIFY]\". Put anything unclear "
        "in open_questions instead of guessing."),
    input_schema={
        "type": "object",
        "properties": {
            "findings": {"type": "string",
                         "description": "The deliverable itself: the requested list, table, "
                                        "draft or answer, in full but concise."},
            "figures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": ["string", "number"]},
                        "unit": {"type": "string"},
                        "period": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["label", "value", "source"],
                },
            },
            "open_questions": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["findings", "confidence"],
    },
    handler=_submit_result,
)


# report_to_captain ------------------------------------------------------------------------
async def _report_to_captain(ctx: ToolContext, inp: dict) -> str:
    text = _str(inp, "text", max_len=8000)
    await ctx.office.report_to_captain(ctx.agent.id, text, task_id=ctx.task["id"])
    return f"Sent to {ctx.office.captain_name}."


REPORT_TO_CAPTAIN = Tool(
    name="report_to_captain",
    description=(
        "Send a message to {captain}, the Captain: a finished deliverable summary, a decision "
        "you need from {captain}, or an important risk. Be concise and lead with the answer."),
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    handler=_report_to_captain,
)


# assign_task (Chief of Staff) -------------------------------------------------------------
async def _assign_task(ctx: ToolContext, inp: dict) -> str:
    to = ctx.office.resolve(_str(inp, "to", max_len=100))
    title = _str(inp, "title", max_len=120)
    brief = _str(inp, "brief", max_len=8000)
    target = ctx.office.agents[to]
    if target.tier != "lead":
        raise GuardBlock(f"{target.nickname} is an associate. Assign work to department leads; "
                         "they delegate to their associates.")
    ctx.agent.guard.on_delegate()   # fan-out counts against the same per-task limit
    task_id = ctx.office.assign(to, brief, title=title, by=ctx.agent.id, parent=ctx.task)
    ctx.office.bus.publish("move", ctx.agent.id, ctx.task["id"], to=f"desk:{to}")
    ctx.office.bus.publish("move", ctx.agent.id, ctx.task["id"], to=f"desk:{ctx.agent.id}")
    return f"Assigned to {target.nickname} (task #{task_id})."


ASSIGN_TASK = Tool(
    name="assign_task",
    description=(
        "Give a department lead a new assignment on {captain}'s behalf. State the goal, the "
        "deliverable, and any deadline or constraint {captain} gave, without dropping or "
        "changing any of it. One lead per call; call again for other wings."),
    input_schema={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Lead id or nickname."},
            "title": {"type": "string", "description": "Short task title."},
            "brief": {"type": "string", "description": "The assignment, complete and specific."},
        },
        "required": ["to", "title", "brief"],
    },
    handler=_assign_task,
)


# request_approval ---------------------------------------------------------------------------
APPROVAL_KINDS = ("brief", "model", "conflict", "portfolio", "newsletter", "other")


async def _request_approval(ctx: ToolContext, inp: dict) -> str:
    kind = inp.get("kind")
    if kind not in APPROVAL_KINDS:
        raise GuardBlock(f"`kind` must be one of: {', '.join(APPROVAL_KINDS)}.")
    title = _str(inp, "title", max_len=120)
    summary = _str(inp, "summary", max_len=6000)
    payload: dict[str, Any] = {}
    ticker = inp.get("ticker")
    if ticker is not None:
        if not isinstance(ticker, str) or not ticker.strip() or len(ticker) > 10:
            raise GuardBlock("`ticker` must be a short symbol like RMBS.")
        payload["ticker"] = ticker.strip().upper()
    version = inp.get("version")
    if version is not None:
        if not isinstance(version, int) or version < 1:
            raise GuardBlock("`version` must be a whole number starting at 1.")
        payload["version"] = version
    if kind == "newsletter":
        lead = ctx.office.agents.get("cr_lead")
        raise GuardBlock(f"A newsletter reaches {ctx.office.captain_name} through finalize_newsletter "
                         f"({lead.nickname if lead else 'the Client Relations lead'}), which runs the "
                         "checks, builds the files and files this card.")
    if kind == "model" and ("ticker" not in payload or "version" not in payload):
        raise GuardBlock("A model approval needs `ticker` and `version`.")
    if kind == "model":
        m = ctx.office.store.model(payload["ticker"], payload["version"])
        if m is None:
            raise GuardBlock(f"No model v{payload['version']} for {payload['ticker']}. Build it first.")
        if not m["summary"].get("check", {}).get("ok"):
            raise GuardBlock("That version failed its formula check; fix it before it goes to "
                             "the Captain.")
        if m["status"] not in ("draft", "changes"):
            raise GuardBlock(f"v{payload['version']} is already {m['status']}.")
    attachments = inp.get("attachments") or []
    if not isinstance(attachments, list) or not all(isinstance(a, str) for a in attachments):
        raise GuardBlock("`attachments` must be a list of file paths.")
    payload["attachments"] = attachments[:10]
    approval_id = ctx.office.request_approval(ctx.agent.id, kind=kind, title=title,
                                              summary=summary, payload=payload,
                                              task_id=ctx.task["id"])
    return (f"Approval #{approval_id} is on {ctx.office.captain_name}'s desk. The decision will "
            "arrive as a message; don't wait for it. Wrap up this task.")


REQUEST_APPROVAL = Tool(
    name="request_approval",
    description=(
        "Put a decision on {captain}'s desk as an approval card: a research brief to review "
        "(kind brief), a model version to make official (kind model, with ticker and version), "
        "a model that contradicts the thesis (kind conflict, numbers attached), a portfolio "
        "entry, or other. Lead the summary with what you need decided and why. "
        "The decision comes back to you as a message later."),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": [k for k in APPROVAL_KINDS if k != "newsletter"]},
            "title": {"type": "string"},
            "summary": {"type": "string",
                        "description": "What needs deciding, the key numbers, and risks."},
            "ticker": {"type": "string"},
            "version": {"type": "integer", "description": "Model version (kind model)."},
            "attachments": {"type": "array", "items": {"type": "string"},
                            "description": "Paths of files to review (workbook, brief...)."},
        },
        "required": ["kind", "title", "summary"],
    },
    handler=_request_approval,
)


# post_to_group ------------------------------------------------------------------------------
async def _post_to_group(ctx: ToolContext, inp: dict) -> str:
    group = inp.get("group")
    text = _str(inp, "text", max_len=2000)
    ctx.agent.guard.check_group_post()
    ctx.office.post_to_group(ctx.agent.id, group, text, task_id=ctx.task["id"])   # may refuse
    ctx.agent.guard.record_group_post()
    if ctx.agent.id == "chief_of_staff" and group == "juno":
        return "Announced to the whole office. Replies will appear in the group."
    return "Posted in the group."


POST_TO_GROUP = Tool(
    name="post_to_group",
    description=(
        "Post in an office-wide group chat. Group \"stott\" is {captain}'s announcement thread; "
        "group \"juno\" is the Chief of Staff's. Use it to reply to an announcement (one short "
        "line) or, for the Chief of Staff only, to announce something to everyone in group "
        "\"juno\" (sparingly: every colleague replies)."),
    input_schema={
        "type": "object",
        "properties": {
            "group": {"type": "string", "enum": ["stott", "juno"]},
            "text": {"type": "string"},
        },
        "required": ["group", "text"],
    },
    handler=_post_to_group,
)


# note_to_self --------------------------------------------------------------------------------
async def _note_to_self(ctx: ToolContext, inp: dict) -> str:
    from hq.memory import NOTE_CHARS

    note = _str(inp, "note", max_len=NOTE_CHARS)
    ctx.agent.guard.check_note()
    out = ctx.office.memory_write(ctx.agent.id, "desk", note, task_id=ctx.task["id"])
    ctx.agent.guard.record_note()
    if out["held"]:
        return (f"Not saved yet: the note {' and '.join(out['reasons'])}, so it waits for "
                f"{ctx.office.captain_name}'s review. Carry on; don't re-send it.")
    return "Saved to your desk notes; you'll see it at the start of future tasks."


NOTE_TO_SELF = Tool(
    name="note_to_self",
    description=(
        "Save a short note to your desk for future tasks: a lesson, a preference {captain} "
        "expressed, where something lives. Keep it to one or two sentences; don't store figures "
        "that belong in the model or the brief."),
    input_schema={"type": "object", "properties": {"note": {"type": "string"}},
                  "required": ["note"]},
    handler=_note_to_self,
)


# propose_wiki --------------------------------------------------------------------------------
async def _propose_wiki(ctx: ToolContext, inp: dict) -> str:
    entry = _str(inp, "entry", max_len=300)
    ctx.agent.guard.check_note()
    out = ctx.office.memory_write(ctx.agent.id, "wiki", entry, task_id=ctx.task["id"])
    ctx.agent.guard.record_note()
    return (f"Proposal #{out['id']} is waiting for {ctx.office.captain_name}. The wiki only "
            "changes if he approves it; carry on.")


PROPOSE_WIKI = Tool(
    name="propose_wiki",
    description=(
        "Propose one short entry for the office wiki, the standing guidance every colleague "
        "reads: a preference {captain} stated, or a firm-wide rule worth keeping. One sentence. "
        "It is added only if {captain} approves it. Not for figures, and not for anything that "
        "only matters to your own desk (use note_to_self)."),
    input_schema={"type": "object", "properties": {"entry": {"type": "string"}},
                  "required": ["entry"]},
    handler=_propose_wiki,
)


def tools_for(tier: str, agent_id: str, wing: str | None = None) -> list[Tool]:
    """The fixed tool list for an agent. It never changes during a task (preserved thinking).
    With `wing`, the wing's desk tools (research, quant) are added after the office tools."""
    base = _office_tools(tier, agent_id)
    if wing is None:
        return base
    base = base + [NOTE_TO_SELF]
    if tier == "lead" or agent_id == "chief_of_staff":
        base = base + [PROPOSE_WIKI]
    from hq.tools.desk import desk_tools
    return base + [t for t in desk_tools(wing, tier) if t.name not in {b.name for b in base}]


def _office_tools(tier: str, agent_id: str) -> list[Tool]:
    if agent_id == "chief_of_staff":
        return [SEND_MESSAGE, ASSIGN_TASK, REPORT_TO_CAPTAIN, REQUEST_APPROVAL, POST_TO_GROUP]
    if tier == "associate":
        return [SEND_MESSAGE, SUBMIT_RESULT, POST_TO_GROUP]
    return [SEND_MESSAGE, DELEGATE, REPORT_TO_CAPTAIN, REQUEST_APPROVAL, POST_TO_GROUP]


def tool_map(tools: list[Tool]) -> dict[str, Tool]:
    return {t.name: t for t in tools}


def definitions(tools: list[Tool], captain: str = "the Captain") -> list[dict[str, Any]]:
    defs = [t.definition() for t in tools]
    for d in defs:
        d["description"] = d["description"].replace("{captain}", captain)
    return defs
