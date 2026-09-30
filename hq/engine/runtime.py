"""The Office: agents, scheduling, messaging, delegation, pause/resume and clock-out.

The office only works when the Captain assigns something. Each agent handles one task at a time
at its desk. Model calls share a concurrency limit. Idle agents cost nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
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
GROUP_STOTT = "group:stott"   # Stott -> all: the Captain's announcements and the replies
GROUP_JUNO = "group:juno"     # Juno -> all: the Chief of Staff's announcements and the replies
GROUPS = {"stott": GROUP_STOTT, "juno": GROUP_JUNO}
THREAD_CONTEXT = 6   # recent messages between two colleagues included when a reply starts


class Office:
    def __init__(self, *, store: Store | None = None, llm: ModelClient | None = None,
                 ledger: Ledger | None = None, db_path: Path | str | None = None,
                 tone: str | None = None):
        self.store = store or Store(db_path or DATA_DIR / "office.db")
        self.bus = EventBus(self.store)
        self.ledger = ledger or Ledger(self.store)
        self._llm = llm
        self.slots = asyncio.Semaphore(office()["limits"]["max_concurrent_agents"])
        self.conversations = ConversationGuard()
        self.clocked_out = False
        self._running: set[asyncio.Task] = set()
        # The Captain's tone filter: "llm" (Haiku, billed to Juno) or "rules" (free, demo).
        self.tone_engine = tone or office().get("tone", {}).get("engine", "llm")
        cast = roster()
        self._wings: dict[str, str] = {k: v["name"] for k, v in cast["wings"].items()}
        self.captain_name: str = cast.get("captain", {}).get("nickname") or "the Captain"
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
        return self.captain_name if who == CAPTAIN else self.agents[who].nickname

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
               by: str = CAPTAIN, parent: dict | None = None) -> int:
        """Create and schedule an assignment. `parent` (the assigning agent's task) makes the
        new task part of the same piece of work, so it shares that work's $ cap."""
        agent_id = self.resolve(agent_id)
        title = title or _title(body)
        task_id = self.store.create_task(
            assignee=agent_id, assigned_by=by, kind="assignment", title=title, body=body,
            parent_id=parent["id"] if parent else None,
            root_id=parent["root_id"] if parent else None)
        if by == CAPTAIN:
            self.conversations.record_message(CAPTAIN, [agent_id], body)
            self.store.add_chat(channel=f"dm:{CAPTAIN}|{agent_id}", sender=CAPTAIN,
                                recipients=[agent_id], text=body, task_id=task_id)
            self.bus.publish("captain_message", agent_id, task_id, to=agent_id, text=body,
                             delivered="task")
        elif by in self.agents:   # e.g. Juno assigning on the Captain's behalf
            channel = _channel(by, [agent_id], self.agents)
            text = f"New assignment: {title}\n\n{body}"
            self.store.add_chat(channel=channel, sender=by, recipients=[agent_id], text=text,
                                task_id=task_id)
            self.bus.publish("chat", by, task_id, channel=channel, recipients=[agent_id], text=text)
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
        if channel == "lobby":
            # A cross-wing group message gathers everyone at the Lobby table.
            self.bus.publish("meeting", sender, task_id, participants=[sender, *recipients])
        elif away:
            self.bus.publish("move", sender, task_id, to=f"desk:{away[0]}")
        self.bus.publish("chat", sender, task_id, channel=channel, recipients=recipients,
                         text=text)
        if away and channel != "lobby":
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

    # the Captain's channel ---------------------------------------------------------------
    def captain_send(self, to: str, text: str, *, original: str | None = None) -> dict:
        """Deliver a message from the Captain. `to` is "office" (Juno routes it) or an agent.

        `original` is the Captain's own wording when he sent the tone rewrite: it is kept in the
        log for him, and never shown to agents.
        """
        kept = original if original and original.strip() != text.strip() else None
        if to == "all":
            self.store.add_chat(channel=GROUP_STOTT, sender=CAPTAIN,
                                recipients=list(self.agents), text=text, original=kept)
            self.bus.publish("captain_message", None, None, to="all", text=text, original=kept)
            self.bus.publish("chat", CAPTAIN, None, channel=GROUP_STOTT,
                             recipients=list(self.agents), text=text)
            tasks = self._announce(CAPTAIN, "stott", text)
            return {"routed_to": "all", "task_ids": tasks}
        if to == "office":
            juno = "chief_of_staff"
            body = (f"{self.captain_name} sent a message to the whole office:\n\n{text}\n\n"
                    "Route it: use assign_task to give the right lead(s) a clear assignment (goal, "
                    "deliverable, any deadline), or answer it yourself if it's a question for you. "
                    f"Then tell {self.captain_name} in one line who is on it.")
            task_id = self.store.create_task(assignee=juno, assigned_by=CAPTAIN, kind="assignment",
                                             title=_title(text), body=body)
            self.conversations.record_message(CAPTAIN, [juno], text)
            self.store.add_chat(channel="captain:office", sender=CAPTAIN, recipients=[juno],
                                text=text, task_id=task_id, original=kept)
            self.bus.publish("captain_message", juno, task_id, to="office", text=text,
                             original=kept)   # UI only (the Captain's log); agents never see it
            self.bus.publish("task_created", juno, task_id, title=_title(text), kind="assignment",
                             assigned_by=CAPTAIN)
            self._schedule(juno, task_id)
            return {"routed_to": juno, "task_id": task_id}

        agent_id = self.resolve(to)
        agent = self.agents[agent_id]
        self.conversations.record_message(CAPTAIN, [agent_id], text)
        if agent.current_task is not None or agent.desk.locked():
            # Busy: the message lands in their next turn instead of starting a new task.
            agent.inbox.append(f"[Message from {self.captain_name} (the Captain)]: {text}")
            self.store.add_chat(channel=f"dm:{CAPTAIN}|{agent_id}", sender=CAPTAIN,
                                recipients=[agent_id], text=text, task_id=agent.current_task,
                                original=kept)
            self.bus.publish("captain_message", agent_id, agent.current_task, to=agent_id,
                             text=text, delivered="inbox", original=kept)
            return {"routed_to": agent_id, "task_id": agent.current_task, "delivered": "inbox"}
        task_id = self.store.create_task(assignee=agent_id, assigned_by=CAPTAIN,
                                         kind="assignment", title=_title(text), body=text)
        self.store.add_chat(channel=f"dm:{CAPTAIN}|{agent_id}", sender=CAPTAIN,
                            recipients=[agent_id], text=text, task_id=task_id, original=kept)
        self.bus.publish("captain_message", agent_id, task_id, to=agent_id, text=text,
                         delivered="task", original=kept)
        self.bus.publish("task_created", agent_id, task_id, title=_title(text), kind="assignment",
                         assigned_by=CAPTAIN)
        self._schedule(agent_id, task_id)
        return {"routed_to": agent_id, "task_id": task_id, "delivered": "task"}

    async def tone_preview(self, to: str, text: str) -> dict:
        """Rewrite a Captain message for the team and check nothing important changed."""
        from hq.engine.tone import LLMRewriter, RuleRewriter, check

        recipient = "" if to in ("office", "all") else self.agents[self.resolve(to)].nickname
        rewriter = LLMRewriter(self) if self.tone_engine == "llm" else RuleRewriter()
        from hq.engine.llm import ApiDisabled

        try:
            rewrite = await rewriter.rewrite(text, recipient)
            engine = rewriter.engine
        except BudgetExhausted:
            rewrite, engine = await RuleRewriter().rewrite(text, recipient), "rules (budget)"
        except ApiDisabled:
            rewrite, engine = await RuleRewriter().rewrite(text, recipient), "rules (API off)"
        return {"original": text, "rewrite": rewrite, "engine": engine,
                "check": check(text, rewrite).as_dict()}

    # group chats ------------------------------------------------------------------------
    def _announce(self, sender: str, group: str, text: str) -> list[int]:
        """Deliver an announcement to every agent except the sender. Each replies once in the
        group; replies are posts, never new announcements, so nothing fans out twice."""
        who = self.name(sender)
        note = (f"[Announcement from {who} to the whole office]: {text}\n\nReply once in the "
                f'group with post_to_group (group "{group}"): one short line on what it means for '
                "your work, or a question. Then carry on.")
        tasks = []
        for agent in self.agents.values():
            if agent.id == sender:
                continue
            if agent.current_task is not None or agent.desk.locked():
                agent.inbox.append(note)
                continue
            task_id = self.store.create_task(assignee=agent.id, assigned_by=sender,
                                             kind="message", title=f"Announcement from {who}",
                                             body=note)
            self._schedule(agent.id, task_id)
            tasks.append(task_id)
        return tasks

    def post_to_group(self, sender: str, group: str, text: str,
                      task_id: int | None = None) -> None:
        """Post in a group chat. Juno posting in her own group is an announcement to everyone;
        every other post is a reply that stays in the thread."""
        if group not in GROUPS:
            raise GuardBlock(f"Unknown group {group!r}. Groups: {', '.join(GROUPS)}.")
        channel = GROUPS[group]
        self.conversations.check_message(sender, [CAPTAIN], text)   # repeat guard
        self.conversations.record_message(sender, [CAPTAIN], text)
        everyone = [a for a in self.agents if a != sender]
        self.store.add_chat(channel=channel, sender=sender, recipients=everyone, text=text,
                            task_id=task_id)
        self.bus.publish("chat", sender, task_id, channel=channel, recipients=everyone, text=text)
        if sender == "chief_of_staff" and group == "juno":
            self._announce(sender, "juno", text)

    def request_approval(self, agent_id: str, *, kind: str, title: str, summary: str,
                         payload: dict, task_id: int | None) -> int:
        approval_id = self.store.add_approval(kind=kind, agent=agent_id, task_id=task_id,
                                              title=title, summary=summary, payload=payload)
        self.bus.publish("approval_requested", agent_id, task_id, approval=approval_id, kind=kind,
                         title=title)
        return approval_id

    def decide(self, approval_id: int, decision: str, note: str | None = None) -> dict:
        """The Captain's decision on an approval card, sent back to the agent who asked."""
        if decision not in ("approved", "changes", "rejected"):
            raise ValueError("decision must be approved, changes or rejected")
        card = self.store.approval(approval_id)
        if card["status"] != "pending":
            raise ValueError(f"approval {approval_id} is already {card['status']}")
        self.store.decide_approval(approval_id, decision, note)
        verb = {"approved": "approved", "changes": "asked for changes on",
                "rejected": "declined"}[decision]
        text = f'{self.captain_name} {verb} your request "{card["title"]}" (approval #{approval_id}).'
        if note:
            text += f" Note: {note}"
        if decision == "approved" and card["kind"] == "model":
            text += (" This version is now the firm's official numbers; distribute it to the "
                     "teams covering this equity.")
        self.bus.publish("approval_decided", card["agent"], card["task_id"], approval=approval_id,
                         decision=decision, note=note, title=card["title"])
        agent = self.agents.get(card["agent"])
        if agent is not None:
            self.store.add_chat(channel=f"dm:{CAPTAIN}|{agent.id}", sender=CAPTAIN,
                                recipients=[agent.id], text=text, task_id=card["task_id"])
            self.bus.publish("captain_message", agent.id, card["task_id"], to=agent.id, text=text,
                             delivered="decision")
            if agent.current_task is not None or agent.desk.locked():
                agent.inbox.append(f"[Message from {self.captain_name} (the Captain)]: {text}")
            else:
                task_id = self.store.create_task(
                    assignee=agent.id, assigned_by=CAPTAIN, kind="message",
                    title=f"Decision on: {card['title']}"[:70],
                    body=f"[Message from {self.captain_name} (the Captain)]: {text}\n\n"
                         "Act on the decision (finish, revise, or distribute), then end with a "
                         "one-line recap.")
                self._schedule(agent.id, task_id)
        return self.store.approval(approval_id)

    def raise_incident(self, agent: str | None, task_id: int | None, kind: str, detail: str,
                       **extra) -> int:
        """Record an incident for the Captain and announce it (with its id, so the UI can
        acknowledge exactly this one)."""
        incident_id = self.store.add_incident(agent=agent, task_id=task_id, kind=kind,
                                              detail=detail)
        self.bus.publish("incident", agent, task_id, incident_id=incident_id, kind=kind,
                         detail=detail, **extra)
        return incident_id

    def documents(self, agent_id: str) -> list[dict]:
        """An agent's document history, newest first: what they put on the Captain's desk
        (approval submissions with attachments), their reports, and the results they handed
        back to a lead. Phase 4 adds real files (models, memos) to the same list."""
        agent_id = self.resolve(agent_id)
        docs: list[dict] = []
        for a in self.store.approvals():
            if a["agent"] == agent_id:
                docs.append({"ts": a["ts"], "kind": a["kind"], "title": a["title"],
                             "text": a["summary"], "status": a["status"], "note": a["note"],
                             "attachments": a["payload"].get("attachments", []),
                             "ticker": a["payload"].get("ticker"),
                             "version": a["payload"].get("version"), "source": "approval"})
        for m in self.store.chat(f"dm:{CAPTAIN}|{agent_id}", limit=500):
            if m["sender"] == agent_id:
                docs.append({"ts": m["ts"], "kind": "report", "title": _title(m["text"]),
                             "text": m["text"], "source": "report"})
        for t in self.store.tasks("done"):
            if t["assignee"] == agent_id and t["kind"] == "delegation" and t["result"]:
                try:
                    result = json.loads(t["result"])
                except ValueError:
                    result = None
                if not isinstance(result, dict):   # plain text (or odd JSON) from the associate
                    result = {"findings": t["result"]}
                docs.append({"ts": t["updated"], "kind": "result", "title": t["title"],
                             "text": result.get("findings", ""), "source": "delegation",
                             "for": t["assigned_by"]})
        return sorted(docs, key=lambda d: d["ts"], reverse=True)

    def resolve_incident(self, incident_id: int) -> None:
        self.store.resolve_incident(incident_id)
        self.bus.publish("incident_resolved", None, None, incident=incident_id)

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
            self.raise_incident(agent.id, agent.current_task, "paused",
                                f"Paused by {self.name(by)}: {reason}", by=by)

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

    def snapshot(self, events: int = 300, chat: int = 200) -> dict:
        """Everything the office UI needs on connect."""
        cast = roster()
        entries = {e["id"]: e for e in cast["agents"]}
        day = self.ledger.today()
        breakdown = self.store.spend_breakdown(day)
        by_agent: dict[str, float] = {}
        by_model: dict[str, float] = {}
        for row in breakdown:
            by_agent[row["agent"]] = by_agent.get(row["agent"], 0) + row["cost"]
            by_model[row["model"]] = by_model.get(row["model"], 0) + row["cost"]
        agents = []
        for a in self.agents.values():
            task = self.store.task(a.current_task) if a.current_task else None
            agents.append({
                "id": a.id, "nickname": a.nickname, "wing": a.wing, "role": a.role,
                "persona": a.persona, "model": a.model_key, "model_id": a.model_cfg["id"],
                "tier": a.tier, "avatar": entries.get(a.id, {}).get("avatar"),
                "status": a.status, "paused": a.paused,
                "task": {"id": task["id"], "title": task["title"]} if task else None,
            })
        return {
            "office": {"clocked_out": self.clocked_out, "day": day,
                       "max_concurrent": office()["limits"]["max_concurrent_agents"]},
            "wings": self._wings,
            "captain": cast.get("captain", {"nickname": "Captain"}),
            "models": {k: v["id"] for k, v in office()["models"].items()},
            "agents": agents,
            "spend": {"today": round(self.ledger.spent_today(), 6),
                      "cap": self.ledger.daily_cap, "by_agent": by_agent,
                      "by_model": by_model},
            "events": [{"id": e["id"], "ts": e["ts"], "type": e["type"], "agent": e["agent"],
                        "task_id": e["task_id"], **e["payload"]}
                       for e in self.store.events(limit=events)],
            "chat": self.store.chat(limit=chat),
            "incidents": self.store.incidents(),
            "approvals": self.store.approvals(),
            "captain_name": self.captain_name,
        }

    def reload_roster(self) -> None:
        """Apply roster.yaml edits (nicknames, personas, models). Running tasks keep the prompt
        they started with; new tasks use the new profile."""
        cast = roster()
        self._wings = {k: v["name"] for k, v in cast["wings"].items()}
        self.captain_name = cast.get("captain", {}).get("nickname") or "the Captain"
        for e in cast["agents"]:
            if e["id"] in self.agents:
                self.agents[e["id"]].update_profile(e)
            else:
                self.agents[e["id"]] = Agent(self, e)


_GREETING = re.compile(r"^(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b", re.IGNORECASE)


def _title(text: str, n: int = 70) -> str:
    """A task title from a message: its first real line, skipping a greeting paragraph."""
    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    if len(paragraphs) > 1 and _GREETING.match(paragraphs[0]) and len(paragraphs[0]) < 120:
        paragraphs = paragraphs[1:]
    line = paragraphs[0].splitlines()[0] if paragraphs else "Untitled"
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
