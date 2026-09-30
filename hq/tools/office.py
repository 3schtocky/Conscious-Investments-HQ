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


def tools_for(tier: str, agent_id: str) -> list[Tool]:
    """The fixed tool list for an agent. It never changes during a task (preserved thinking)."""
    if tier == "associate" and agent_id != "chief_of_staff":
        return [SEND_MESSAGE, SUBMIT_RESULT]
    tools = [SEND_MESSAGE, REPORT_TO_CAPTAIN]
    if tier == "lead":
        tools.insert(1, DELEGATE)
    return tools


def tool_map(tools: list[Tool]) -> dict[str, Tool]:
    return {t.name: t for t in tools}


def definitions(tools: list[Tool], captain: str = "the Captain") -> list[dict[str, Any]]:
    defs = [t.definition() for t in tools]
    for d in defs:
        d["description"] = d["description"].replace("{captain}", captain)
    return defs
