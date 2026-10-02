"""One office agent: identity, fixed tools, and the streaming tool-use loop for a task.

Loop invariants:
- The conversation is append-only and saved after every change, so a paused task resumes
  exactly where it stopped (and Sonnet 5.5 thinking blocks stay valid).
- The system prompt and tool list are frozen at task start. Roster edits apply to new tasks.
- Before every model call: pause gate, daily budget, then per-task guards. A call already in
  flight always finishes and is recorded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from hq.config import ROOT, mission, model_config, office
from hq.engine.guards import GuardBlock, GuardTripped, TaskGuard
from hq.engine.ledger import BudgetExhausted
from hq.engine.llm import ApiDisabled, TurnResult, request_params, web_tools
from hq.tools.office import ToolContext, definitions, tool_map, tools_for

if TYPE_CHECKING:
    from hq.engine.runtime import Office

log = logging.getLogger(__name__)
PROMPTS = ROOT / "prompts"
TERMINAL = {"done", "declined"}
TRUNCATED_NOTE = ("[Office] Your last reply hit the length limit and was cut off. Continue from "
                  "where it stopped, more concisely.")
NOTES_LIMIT = 3000   # chars of associate text passed along with a submitted result


class Agent:
    def __init__(self, office_: Office, entry: dict):
        self.office = office_
        self.id: str = entry["id"]
        self.update_profile(entry)
        self.desk = asyncio.Lock()          # one task at a time
        self.gate = asyncio.Event()         # cleared = paused
        self.gate.set()
        self.inbox: list[str] = []
        self.current_task: int | None = None
        self.guard: TaskGuard | None = None
        self.status = "idle"
        self.paused_by: str | None = None   # who paused this agent (the Captain or Audit)

    def update_profile(self, entry: dict) -> None:
        self.nickname: str = entry["nickname"]
        self.wing: str = entry["wing"]
        self.role: str = entry["role"]
        self.persona: str = entry.get("persona", "").strip()
        self.model_key: str = entry.get("model", "lead")
        self.tier, self.model_cfg = model_config(self.model_key, tier=entry.get("tier"))

    @property
    def is_audit(self) -> bool:
        return self.wing == "audit"

    @property
    def paused(self) -> bool:
        return not self.gate.is_set()

    # prompts ----------------------------------------------------------------------------
    def system_prompt(self) -> str:
        role_file = "chief_of_staff.md" if self.id == "chief_of_staff" else f"{self.tier}.md"
        team = "\n".join(
            f"- {a.nickname} (`{a.id}`): {a.role}, {self.office.wing_name(a.wing)}"
            for a in self.office.agents.values() if a.id != self.id)
        charter = PROMPTS / "departments" / f"{self.wing}.md"   # optional department charter
        parts = ["\n\n".join([
            mission().strip(),
            (PROMPTS / "common.md").read_text().strip(),
            (PROMPTS / role_file).read_text().strip(),
            *([charter.read_text().strip()] if charter.is_file() else []),
            (f"# Who you are\nYou are **{self.nickname}** (`{self.id}`), {self.role} in the "
             f"{self.office.wing_name(self.wing)} wing.\n\n{self.persona}"),
            f"# Your colleagues\n{team}",
            *self._memory_sections(),
        ])]
        return parts[0].replace("{captain}", self.office.captain_name)

    def _memory_sections(self) -> list[str]:
        from hq.memory import desk_notes, wiki

        out = []
        w = wiki(self.office.memory_dir)
        if w:
            out.append(f"# Office wiki (standing guidance from {{captain}})\n{w}")
        notes = desk_notes(self.id, self.office.memory_dir)
        if notes:
            out.append(f"# Your desk notes (from earlier tasks)\n{notes}")
        return out

    # status -----------------------------------------------------------------------------
    def set_status(self, status: str, **extra: Any) -> None:
        self.status = status
        self.office.bus.publish("status", self.id, self.current_task, status=status, **extra)

    # the loop ---------------------------------------------------------------------------
    async def work(self, task_id: int) -> dict:
        """Run a task at this agent's desk (waits if the desk is busy)."""
        async with self.desk:
            self.current_task = task_id
            try:
                return await self._run(task_id)
            finally:
                self.current_task = None
                self.guard = None
                if self.is_audit:   # a review that ended without a ruling must not hide its flags
                    self.office.reopen_unreviewed(task_id)
                if not self.paused:
                    self.set_status("idle")
                if self.inbox:   # messages that arrived as the task ended get their own turn
                    self.office.spawn_inbox_task(self.id)

    def _task_guard(self, task: dict, messages: list[dict]) -> TaskGuard:
        cfg = office()
        if task["kind"] == "delegation":
            d = cfg["delegation"]
            guard = TaskGuard(max_turns=d["max_turns"], cost_cap=d["per_delegation_cap_usd"],
                              max_context_tokens=d["max_context_tokens"])
        else:
            guard = TaskGuard(max_turns=cfg["limits"]["max_turns_per_task"],
                              cost_cap=cfg["budget"]["per_task_cap_usd"])
        guard.turns = sum(1 for m in messages if m["role"] == "assistant")
        return guard

    def _task_spend(self, task: dict) -> float:
        store = self.office.store
        if task["kind"] == "delegation":
            return store.spend_for_task(task["id"])
        return store.spend_for_root(task["root_id"])

    async def _run(self, task_id: int) -> dict:
        office_ = self.office
        store, bus = office_.store, office_.bus
        task = store.task(task_id)
        if task["status"] in TERMINAL:   # e.g. scheduled twice; never re-run finished work
            return {"status": task["status"], "reason": "already finished"}
        saved = store.conversation(task_id)
        if saved:
            system, messages = saved
        else:
            system = self.system_prompt()
            messages = [{"role": "user", "content": office_.render_task(task)}]
            store.save_conversation(task_id, system, messages)
        model_cfg = self.model_cfg   # frozen for this run, like the prompt and tools
        tools = tools_for(self.tier, self.id, self.wing)
        by_name = tool_map(tools)
        tool_defs = definitions(tools, captain=office_.captain_name) + web_tools(model_cfg, self.wing, self.id)
        self.guard = guard = self._task_guard(task, messages)

        store.set_task_status(task_id, "running")
        bus.publish("task_started", self.id, task_id, title=task["title"], kind=task["kind"],
                    assigned_by=task["assigned_by"], resumed=bool(saved))
        self.set_status("working", title=task["title"])

        last_stop: str | None = None
        try:
            while True:
                if messages[-1]["role"] == "assistant" and last_stop != "pause_turn":
                    ctx = ToolContext(office_, self, task)
                    follow_up = await self._after_assistant(ctx, messages[-1], by_name,
                                                            truncated=last_stop == "max_tokens")
                    if ctx.finished is not None:
                        notes = _final_text(messages[-1])
                        if notes:   # text written alongside submit_result travels with it
                            ctx.finished["notes"] = notes[:NOTES_LIMIT]
                        if follow_up:
                            messages.append({"role": "user", "content": follow_up})
                            store.save_conversation(task_id, system, messages)
                        return self._finish(task, result=ctx.finished)
                    if follow_up is None:
                        final = _final_text(messages[-1])
                        return self._finish(task, result=final)
                    messages.append({"role": "user", "content": follow_up})
                    store.save_conversation(task_id, system, messages)
                    continue

                # Model call ------------------------------------------------------------
                if self.paused:
                    self.set_status("paused")
                    await self.gate.wait()
                    self.set_status("working", title=task["title"])
                office_.ledger.check(is_audit=self.is_audit)
                guard.before_turn(self._task_spend(task))
                params = request_params(model_cfg, system=system, messages=messages,
                                        tools=tool_defs)
                async with office_.slots:
                    result = await office_.llm.turn(
                        params=params,
                        on_delta=lambda kind, text: bus.publish(
                            f"{kind}_delta", self.id, task_id, text=text),
                        on_block=lambda block: self._publish_block(task_id, block),
                    )
                self._record(task, params["model"], result)
                messages.append({"role": "assistant", "content": result.content})
                store.save_conversation(task_id, system, messages)
                last_stop = result.stop_reason

                if result.stop_reason == "refusal":
                    detail = (result.stop_details or {}).get("category") or "unspecified"
                    office_.raise_incident(self.id, task_id, "refusal",
                                           f"Model declined (category: {detail}).")
                    store.set_task_status(task_id, "declined", reason=f"refusal: {detail}")
                    return {"status": "declined", "reason": detail}
                guard.after_turn(result.context_tokens)

        except BudgetExhausted as e:
            store.set_task_status(task_id, "paused_budget", reason=str(e))
            bus.publish("task_paused", self.id, task_id, reason="budget", detail=str(e))
            office_.on_budget_exhausted(str(e))
            return {"status": "paused_budget", "reason": str(e)}
        except ApiDisabled as e:
            # The master API switch is off: pause (not an error) so the task can resume later.
            store.set_task_status(task_id, "paused", reason=f"api_off: {e}")
            bus.publish("task_paused", self.id, task_id, reason="api_off", detail=str(e))
            return {"status": "paused", "reason": "api_off", "detail": str(e)}
        except GuardTripped as g:
            store.set_task_status(task_id, "paused", reason=f"{g.kind}: {g.detail}")
            office_.raise_incident(self.id, task_id, g.kind, g.detail)
            bus.publish("task_paused", self.id, task_id, reason=g.kind, detail=g.detail)
            return {"status": "paused", "reason": g.kind, "detail": g.detail}
        except asyncio.CancelledError:
            store.set_task_status(task_id, "paused", reason="cancelled")
            raise
        except Exception as e:   # API errors after SDK retries, bugs: never crash the office
            log.exception("task %s failed", task_id)
            store.set_task_status(task_id, "error", reason=f"{type(e).__name__}: {e}")
            bus.publish("task_error", self.id, task_id, error=f"{type(e).__name__}: {e}")
            return {"status": "error", "reason": str(e)}

    async def _after_assistant(self, ctx: ToolContext, message: dict, by_name: dict, *,
                               truncated: bool = False) -> list[dict] | None:
        """Handle a finished assistant turn. Returns the next user turn's content, or None
        when the task is done (no tools called and no new messages)."""
        tool_uses = [b for b in message["content"] if b.get("type") == "tool_use"]
        if truncated:
            # A cut-off reply: tool inputs may be partial, so none run; the agent continues.
            self.office.bus.publish("truncated", self.id, ctx.task["id"])
            return [{"type": "tool_result", "tool_use_id": b["id"], "is_error": True,
                     "content": "Not run: your reply was cut off mid-call. Send it again."}
                    for b in tool_uses] + [{"type": "text", "text": TRUNCATED_NOTE}]
        # Loop checks for the whole turn come first, so a trip never leaves some calls done
        # and others not (a resumed task would otherwise repeat the finished ones).
        for b in tool_uses:
            if b.get("name") in by_name and isinstance(b.get("input"), dict):
                self.guard.on_tool_call(b["name"], b["input"])
        results = []
        for block in tool_uses:
            results.append(await self._run_tool(ctx, block, by_name))
        if ctx.finished is not None:
            return results   # task ends now; any inbox messages get a follow-up task
        content: list[dict] = results + self._drain_inbox()
        if not tool_uses and not content:
            return None
        return content

    async def _run_tool(self, ctx: ToolContext, block: dict, by_name: dict) -> dict:
        bus = self.office.bus
        name, tool_input = block.get("name"), block.get("input")
        bus.publish("tool_call", self.id, ctx.task["id"], tool=name, input=tool_input)
        tool = by_name.get(name)
        is_error = True
        if tool is None:
            text = f"Unknown tool {name!r}. Available: {', '.join(by_name)}."
        elif not isinstance(tool_input, dict):
            text = "Tool input must be a JSON object."
        else:
            try:
                text = await tool.handler(ctx, tool_input)
                is_error = False
            except GuardBlock as e:
                text = str(e)
                bus.publish("guard_block", self.id, ctx.task["id"], tool=name, reason=text)
            except KeyError as e:
                text = str(e.args[0]) if e.args else "Not found."
            except Exception as e:  # noqa: BLE001 - any tool failure goes back to the agent to fix
                log.warning("tool %s failed: %s", name, e)
                text = f"The tool failed: {type(e).__name__}: {e}"[:1500]
        bus.publish("tool_result", self.id, ctx.task["id"], tool=name, is_error=is_error,
                    text=text[:2000])
        result = {"type": "tool_result", "tool_use_id": block["id"], "content": text}
        if is_error:
            result["is_error"] = True
        return result

    def _drain_inbox(self) -> list[dict]:
        msgs, self.inbox = self.inbox, []
        return [{"type": "text", "text": m} for m in msgs]

    def _publish_block(self, task_id: int, block: dict) -> None:
        kind = block.get("type")
        if kind == "thinking" and block.get("thinking"):
            self.office.bus.publish("thinking", self.id, task_id, text=block["thinking"])
        elif kind == "text" and block.get("text"):
            self.office.bus.publish("text", self.id, task_id, text=block["text"])

    def _record(self, task: dict, model: str, result: TurnResult) -> None:
        cost = self.office.ledger.record(agent=self.id, task_id=task["id"],
                                         root_id=task["root_id"], model=model,
                                         usage=result.usage, request_id=result.request_id)
        self.office.bus.publish(
            "spend", self.id, task["id"], model=model, cost=round(cost, 6),
            usage=result.usage, spent_today=round(self.office.ledger.spent_today(), 4),
            cap=self.office.ledger.daily_cap)

    def _finish(self, task: dict, *, result: Any) -> dict:
        text = result if isinstance(result, str) else json.dumps(result)
        self.office.store.set_task_status(task["id"], "done", result=text)
        self.office.bus.publish("task_done", self.id, task["id"], result=result)
        try:   # Tally's free code checks; a failed check must never cost the work itself
            self.office.audit_task(task)
        except Exception:
            log.exception("audit check failed for task %s", task["id"])
        return {"status": "done", "result": result}


def _final_text(message: dict) -> str:
    return "\n".join(b["text"] for b in message["content"]
                     if b.get("type") == "text" and b.get("text")).strip()
