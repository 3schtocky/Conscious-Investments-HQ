"""The Office: agents, scheduling, messaging, delegation, pause/resume and clock-out.

The office only works when the Captain assigns something. Each agent handles one task at a time
at its desk. Model calls share a concurrency limit. Idle agents cost nothing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from hq.config import DATA_DIR, office, roster
from hq.engine.agent import Agent
from hq.engine.events import EventBus
from hq.engine.guards import ConversationGuard, GuardBlock
from hq.engine.ledger import BudgetExhausted, Ledger
from hq.engine.llm import AnthropicClient, ModelClient
from hq.store import Store

log = logging.getLogger(__name__)

CAPTAIN = "captain"
THREAD_CONTEXT = 6   # recent messages between two colleagues included when a reply starts


class Office:
    def __init__(self, *, store: Store | None = None, llm: ModelClient | None = None,
                 ledger: Ledger | None = None, db_path: Path | str | None = None):
        self.store = store or Store(db_path or DATA_DIR / "office.db")
        self.bus = EventBus(self.store)
        self.ledger = ledger or Ledger(self.store)
        self._llm = llm
        self.slots = asyncio.Semaphore(office()["limits"]["max_concurrent_agents"])
        self.conversations = ConversationGuard()
        self.clocked_out = False
        self._running: set[asyncio.Task] = set()
        cast = roster()
        self._wings: dict[str, str] = {k: v["name"] for k, v in cast["wings"].items()}
        self.agents: dict[str, Agent] = {e["id"]: Agent(self, e) for e in cast["agents"]}

    @property
    def llm(self) -> ModelClient:
        if self._llm is None:   # created lazily so tests and the UI never need an API key
            self._llm = AnthropicClient()
        return self._llm

    # lookups ----------------------------------------------------------------------------
    def wing_name(self, wing: str) -> str:
        return self._wings.get(wing, wing)

    def resolve(self, who: str) -> str:
        """Agent id or nickname (case-insensitive) -> agent id."""
        key = who.strip().lower().lstrip("@")
        for a in self.agents.values():
            if key in (a.id.lower(), a.nickname.lower()):
                return a.id
        raise KeyError(f"No colleague called {who!r}. Colleagues: "
                       + ", ".join(f"{a.nickname} ({a.id})" for a in self.agents.values()))

    def name(self, who: str) -> str:
        return "the Captain" if who == CAPTAIN else self.agents[who].nickname

    def render_task(self, task: dict) -> str:
        by = task["assigned_by"]
        who = self.name(by) if by in self.agents or by == CAPTAIN else by
        if task["kind"] == "delegation":
            return (f"Delegated job from {who}:\n\n{task['body']}\n\n"
                    "When done, call submit_result.")
        if task["kind"] == "message":
            return task["body"]
        return f"New assignment from {who}: **{task['title']}**\n\n{task['body']}"

    # scheduling -------------------------------------------------------------------------
    def assign(self, agent_id: str, body: str, *, title: str | None = None,
               by: str = CAPTAIN) -> int:
        agent_id = self.resolve(agent_id)
        title = title or _title(body)
        task_id = self.store.create_task(assignee=agent_id, assigned_by=by, kind="assignment",
                                         title=title, body=body)
        if by == CAPTAIN:
            self.conversations.record_message(CAPTAIN, [agent_id], body)
            self.store.add_chat(channel=f"dm:{CAPTAIN}|{agent_id}", sender=CAPTAIN,
                                recipients=[agent_id], text=body, task_id=task_id)
        self.bus.publish("task_created", agent_id, task_id, title=title, kind="assignment",
                         assigned_by=by)
        self._schedule(agent_id, task_id)
        return task_id

    def _schedule(self, agent_id: str, task_id: int) -> asyncio.Task:
        t = asyncio.create_task(self.agents[agent_id].work(task_id), name=f"task-{task_id}")
        self._running.add(t)
        t.add_done_callback(self._running.discard)
        return t

    async def idle(self) -> None:
        """Wait until no task is running (used by the CLI and tests)."""
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)

    # messaging --------------------------------------------------------------------------
    async def send_message(self, sender: str, recipients: list[str], text: str,
                           task_id: int | None = None) -> None:
        self.conversations.check_message(sender, recipients, text)   # may raise GuardBlock
        self.conversations.record_message(sender, recipients, text)
        channel = _channel(sender, recipients, self.agents)
        self.store.add_chat(channel=channel, sender=sender, recipients=recipients, text=text,
                            task_id=task_id)
        s = self.agents[sender]
        away = [r for r in recipients if self.agents[r].wing != s.wing]
        if away:
            self.bus.publish("move", sender, task_id, to=f"desk:{away[0]}")
        self.bus.publish("chat", sender, task_id, channel=channel, recipients=recipients,
                         text=text)
        if away:
            self.bus.publish("move", sender, task_id, to=f"desk:{sender}")
        for r in recipients:
            self._deliver(sender, r, text, channel)

    def _deliver(self, sender: str, recipient: str, text: str, channel: str) -> None:
        agent = self.agents[recipient]
        note = f"[Message from {self.name(sender)} ({sender})]: {text}"
        if agent.current_task is not None or agent.desk.locked():
            agent.inbox.append(note)
            return
        # Idle: the message becomes a short task, with the recent thread for context.
        history = [m for m in self.store.chat(channel, limit=THREAD_CONTEXT + 1)][:-1]
        thread = "\n".join(f"{self.name(m['sender'])}: {m['text']}" for m in history)
        body = (f"Recent conversation:\n{thread}\n\n" if thread else "") + note
        body += ("\n\nReply with send_message only if a reply is useful, then end with a "
                 "one-line recap.")
        task_id = self.store.create_task(assignee=recipient, assigned_by=sender, kind="message",
                                         title=f"Message from {self.name(sender)}", body=body)
        self._schedule(recipient, task_id)

    def spawn_inbox_task(self, agent_id: str) -> int | None:
        """Turn messages left in an agent's inbox into a new message task."""
        agent = self.agents[agent_id]
        notes, agent.inbox = agent.inbox, []
        if not notes:
            return None
        body = "\n\n".join(notes) + ("\n\nReply with send_message only if a reply is useful, "
                                     "then end with a one-line recap.")
        task_id = self.store.create_task(assignee=agent_id, assigned_by="office", kind="message",
                                         title="Messages while you were busy", body=body)
        self._schedule(agent_id, task_id)
        return task_id

    async def report_to_captain(self, sender: str, text: str, task_id: int | None = None) -> None:
        self.store.add_chat(channel=f"dm:{CAPTAIN}|{sender}", sender=sender,
                            recipients=[CAPTAIN], text=text, task_id=task_id)
        self.bus.publish("captain_report", sender, task_id, text=text)

    # delegation -------------------------------------------------------------------------
    async def delegate(self, lead: str, associate: str, job: str, *, parent_task: dict) -> dict:
        task_id = self.store.create_task(
            assignee=associate, assigned_by=lead, kind="delegation", title=_title(job),
            body=job, parent_id=parent_task["id"], root_id=parent_task["root_id"])
        self.bus.publish("move", lead, parent_task["id"], to=f"desk:{associate}")
        self.bus.publish("delegated", lead, parent_task["id"], to=associate, task=task_id,
                         job=job)
        self.bus.publish("move", lead, parent_task["id"], to=f"desk:{lead}")
        outcome = await self.agents[associate].work(task_id)
        spent = self.store.spend_for_task(task_id)
        if outcome.get("status") == "done":
            result = outcome["result"]
            if isinstance(result, str):   # ended without submit_result
                result = {"findings": result, "figures": [], "open_questions": [],
                          "confidence": "unstated"}
            return {**result, "delegation_cost_usd": round(spent, 4)}
        return {"status": outcome.get("status"), "reason": outcome.get("reason"),
                "detail": outcome.get("detail"), "delegation_cost_usd": round(spent, 4),
                "note": "The job did not finish. Decide whether to retry, narrow it, or proceed."}

    # control ----------------------------------------------------------------------------
    def pause(self, agent_id: str, *, by: str, reason: str) -> None:
        agent = self.agents[self.resolve(agent_id)]
        agent.gate.clear()
        agent.set_status("paused", by=by, reason=reason)
        if by != CAPTAIN:
            self.store.add_incident(agent=agent.id, task_id=agent.current_task, kind="paused",
                                    detail=f"Paused by {self.name(by)}: {reason}")
            self.bus.publish("incident", agent.id, agent.current_task, kind="paused",
                             detail=reason, by=by)

    def resume(self, agent_id: str, *, by: str) -> None:
        if by != CAPTAIN:
            raise PermissionError("Only the Captain can unpause an agent.")
        agent = self.agents[self.resolve(agent_id)]
        agent.gate.set()
        agent.set_status("working" if agent.current_task else "idle", by=by)

    def on_budget_exhausted(self, detail: str) -> None:
        if not self.clocked_out:
            self.clocked_out = True
            self.bus.publish("office_status", None, None, status="clocked_out", detail=detail)

    def resume_budget_paused(self) -> list[int]:
        """Restart tasks paused by the budget, if today's budget has room (new day or cap
        raised). Returns the task ids restarted."""
        try:
            self.ledger.check()
        except BudgetExhausted:
            return []
        self.clocked_out = False
        restarted = []
        for t in self.store.tasks("paused_budget"):
            if t["kind"] == "delegation":
                continue   # its lead resumes and decides whether to delegate again
            # Mark queued first so a second call (double click, startup + midnight) skips it.
            self.store.set_task_status(t["id"], "queued", reason="resumed after budget reset")
            self._schedule(t["assignee"], t["id"])
            restarted.append(t["id"])
        if restarted:
            self.bus.publish("office_status", None, None, status="open", resumed=restarted)
        return restarted

    def reload_roster(self) -> None:
        """Apply roster.yaml edits (nicknames, personas, models). Running tasks keep the prompt
        they started with; new tasks use the new profile."""
        cast = roster()
        self._wings = {k: v["name"] for k, v in cast["wings"].items()}
        for e in cast["agents"]:
            if e["id"] in self.agents:
                self.agents[e["id"]].update_profile(e)
            else:
                self.agents[e["id"]] = Agent(self, e)


def _title(text: str, n: int = 70) -> str:
    line = text.strip().splitlines()[0] if text.strip() else "Untitled"
    return line if len(line) <= n else line[: n - 1] + "…"


def _channel(sender: str, recipients: list[str], agents: dict[str, Agent]) -> str:
    if len(recipients) == 1:
        a, b = sorted((sender, recipients[0]))
        return f"dm:{a}|{b}"
    wings = {agents[x].wing for x in [sender, *recipients]}
    if len(wings) == 1:
        return f"wing:{wings.pop()}"
    return "lobby"


__all__ = ["CAPTAIN", "GuardBlock", "Office"]
