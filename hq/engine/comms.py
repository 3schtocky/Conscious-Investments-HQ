"""The delegates' comms desk: how wings talk to each other without spending a lead's turns.

Each wing's delegate (its associate, on Haiku) is the wing's head of communication. It reads the
lead's activity from the code-built digest (`hq.digest`), answers status questions, relays work
requests to other wings' leads and speaks for the wing in announcements. A comms exchange is a
short loop of its own, so it runs beside whatever job the delegate has in hand and never touches
that job's desk, conversation or limits.

While the office is paused, or the API is off or out of budget, nobody takes a model turn: an
answer is then the digest read out by code, which costs nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from hq.digest import wing_digest
from hq.engine.guards import GuardBlock, TaskGuard
from hq.engine.ledger import BudgetExhausted
from hq.engine.llm import ApiDisabled, request_params
from hq.tools.office import ToolContext

if TYPE_CHECKING:
    from hq.engine.agent import Agent
    from hq.engine.runtime import Office

log = logging.getLogger(__name__)
COMMS_TURNS = 5       # a comms exchange is a few quick turns, never a long task
COMMS_COST_CAP = 0.05   # USD per exchange: a runaway comms loop stops itself
COMMS_TOKENS = 700    # replies are short; a long answer is a sign of a wrong turn


def comms_wings(office: Office) -> list[str]:
    """Wings run by a lead with a delegate. The Executive Suite (Juno) is not one of them."""
    return [w for w in office._wings if office.lead_of(w) and office.delegate_of(w)]


def is_delegate(office: Office, agent_id: str) -> bool:
    agent = office.agents.get(agent_id)
    return agent is not None and agent.tier == "associate" and agent.wing in comms_wings(office)


class Comms:
    def __init__(self, office: Office):
        self.office = office

    # answers without a model --------------------------------------------------------------
    def code_answer(self, wing: str) -> str:
        """Where the wing stands, read out from the digest. Free; works while paused."""
        d = wing_digest(self.office, wing)
        return f"{d['name']}: {d['headline']}"

    def can_think(self) -> bool:
        """Whether a delegate may take a model turn right now."""
        o = self.office
        return not o.held and not o.clocked_out and o.api_available()

    # entry points -------------------------------------------------------------------------
    def handle(self, delegate_id: str, sender: str, text: str,
               fallback: Callable[[], None] | None = None) -> asyncio.Task | None:
        """A message for a delegate (from its lead or another wing): answer it on the comms desk,
        in the background. Returns None when nobody can take a turn, so the caller queues it;
        `fallback` queues it if the exchange fails after starting (budget, API), so it isn't lost."""
        if not self.can_think():
            return None
        # An announcement arrives already framed; anything else is a message from `sender`.
        note = text if text.startswith("[Announcement from") else (
            f"[Message from {self.office.name(sender)} ({sender})]: {text}")

        async def go() -> None:
            if await self._safe_run(delegate_id, sender, note) is None and fallback:
                fallback()

        task = asyncio.create_task(go(), name=f"comms-{delegate_id}")
        self.office._running.add(task)
        task.add_done_callback(self.office._running.discard)
        return task

    async def ask(self, delegate_id: str, asker: str, question: str, *, depth: int = 0) -> str:
        """Ask a wing's delegate a question and wait for its answer (a status check, a quick
        fact). Falls back to the digest read out by code when no model turn is possible."""
        o = self.office
        agent = o.agents[delegate_id]
        o.say(asker, [delegate_id], question)
        if self.can_think():
            note = f"[Question from {o.name(asker)} ({asker})]: {question}"
            answer = await self._safe_run(delegate_id, asker, note, depth=depth + 1)
        else:
            answer = ""
        if not answer:
            answer = self.code_answer(agent.wing)
        o.say(delegate_id, [asker], answer)
        return answer

    # the comms loop -----------------------------------------------------------------------
    async def _safe_run(self, delegate_id: str, sender: str, note: str,
                        depth: int = 0) -> str | None:
        """The delegate's reply text (possibly empty), or None if no model turn was possible."""
        task_id: list[int] = []
        try:
            return await self._run(delegate_id, sender, note, depth, task_id)
        except (ApiDisabled, BudgetExhausted):
            failed = "no model turn available"
        except Exception as e:
            log.exception("comms for %s failed", delegate_id)
            failed = f"{type(e).__name__}: {e}"
        if task_id:   # never leave the exchange looking unfinished on the board
            self.office.store.set_task_status(task_id[0], "done", reason=failed)
        return None

    async def _run(self, delegate_id: str, sender: str, note: str, depth: int,
                   created: list[int]) -> str:
        from hq.tools.comms import comms_tools  # late: the tools import this module's helpers
        from hq.tools.office import definitions, tool_map

        o = self.office
        agent: Agent = o.agents[delegate_id]
        task_id = o.store.create_task(assignee=delegate_id, assigned_by=sender, kind="comms",
                                      title=f"Comms: {o.name(sender)}", body=note)
        created.append(task_id)
        o.store.set_task_status(task_id, "running")
        task = o.store.task(task_id)
        digest = wing_digest(o, agent.wing)
        extra = ("# Your wing right now (built by code from the live activity; always current)\n"
                 + json.dumps({k: digest[k] for k in
                               ("state", "headline", "blockers", "waiting_on_captain", "people")},
                              indent=1))
        system = agent.system_prompt(role_file="comms.md", extra=extra)
        tools = comms_tools(depth=depth)
        by_name, defs = tool_map(tools), definitions(tools, captain=o.captain_name)
        cfg = {**agent.model_cfg, "thinking_budget_tokens": 0, "max_tokens": COMMS_TOKENS}
        messages: list[dict] = [{"role": "user", "content": note}]
        ctx = ToolContext(o, agent, task, depth=depth,
                          comms_guard=TaskGuard(max_turns=COMMS_TURNS, cost_cap=COMMS_COST_CAP))
        final = ""
        for _ in range(COMMS_TURNS):
            o.ledger.check(is_audit=False)
            params = request_params(cfg, system=system, messages=messages, tools=defs)
            async with o.slots:
                result = await o.llm.turn(params=params)
            cost = o.ledger.record(agent=delegate_id, task_id=task_id, root_id=task["root_id"],
                                   model=params["model"], usage=result.usage,
                                   request_id=result.request_id)
            o.bus.publish("spend", delegate_id, task_id, model=params["model"], cost=round(cost, 6),
                          usage=result.usage, spent_today=round(o.ledger.spent_today(), 4),
                          cap=o.ledger.daily_cap)
            messages.append({"role": "assistant", "content": result.content})
            o.store.save_conversation(task_id, system, messages)
            uses = [b for b in result.content if b.get("type") == "tool_use"]
            final = "\n".join(b["text"] for b in result.content
                              if b.get("type") == "text" and b.get("text")).strip()
            if not uses:
                break
            results = [await self._tool(ctx, b, by_name) for b in uses]
            messages.append({"role": "user", "content": results})
        o.store.set_task_status(task_id, "done", result=final or None)
        return final

    async def _tool(self, ctx: ToolContext, block: dict, by_name: dict) -> dict:
        bus, name = self.office.bus, block.get("name")
        bus.publish("tool_call", ctx.agent.id, ctx.task["id"], tool=name, input=block.get("input"))
        tool, is_error = by_name.get(name), True
        if tool is None:
            text = f"Unknown tool {name!r}. Available: {', '.join(by_name)}."
        elif not isinstance(block.get("input"), dict):
            text = "Tool input must be a JSON object."
        else:
            try:
                text, is_error = await tool.handler(ctx, block["input"]), False
            except GuardBlock as e:
                text = str(e)
            except KeyError as e:
                text = str(e.args[0]) if e.args else "Not found."
            except Exception as e:  # noqa: BLE001 - a failed tool goes back to the agent
                log.warning("comms tool %s failed: %s", name, e)
                text = f"The tool failed: {type(e).__name__}: {e}"[:1500]
        bus.publish("tool_result", ctx.agent.id, ctx.task["id"], tool=name, is_error=is_error,
                    text=text[:2000])
        out = {"type": "tool_result", "tool_use_id": block["id"], "content": text}
        return {**out, "is_error": True} if is_error else out

