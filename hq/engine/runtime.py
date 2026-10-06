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

from hq.config import DATA_DIR, ROOT, office, roster
from hq.engine.agent import Agent
from hq.engine.comms import Comms, is_delegate
from hq.engine.events import EventBus
from hq.engine.guards import ConversationGuard, GuardBlock
from hq.engine.ledger import BudgetExhausted, Ledger
from hq.engine.llm import AnthropicClient, ModelClient
from hq.rounds import Rounds
from hq.store import Store

log = logging.getLogger(__name__)

CAPTAIN = "captain"
OFFICE = "office"             # the author of cards raised by code rather than by a colleague
GROUP_STOTT = "group:stott"   # Stott -> all: the Captain's announcements and the replies
GROUP_JUNO = "group:juno"     # Juno -> all: the Chief of Staff's announcements and the replies
GROUPS = {"stott": GROUP_STOTT, "juno": GROUP_JUNO}
THREAD_CONTEXT = 6   # recent messages between two colleagues included when a reply starts


class Office:
    def __init__(self, *, store: Store | None = None, llm: ModelClient | None = None,
                 ledger: Ledger | None = None, db_path: Path | str | None = None,
                 tone: str | None = None, quant_dir: Path | None = None,
                 memory_dir: Path | None = None, outbox_dir: Path | None = None):
        self.store = store or Store(db_path or DATA_DIR / "office.db")
        self.bus = EventBus(self.store)
        self.ledger = ledger or Ledger(self.store)
        self._llm = llm
        self.slots = asyncio.Semaphore(office()["limits"]["max_concurrent_agents"])
        self.conversations = ConversationGuard()
        self.comms = Comms(self)   # the delegates' comms desk (hq/engine/comms.py)
        self.rounds = Rounds(self)   # Juno's walk of the floor (hq/rounds.py)
        self.clocked_out = False
        self.quant_dir = quant_dir or ROOT / "Quant"   # the Quant Department's model files
        self._model_locks: dict[str, asyncio.Lock] = {}
        from hq.memory import MEMORY_DIR
        self.memory_dir = memory_dir or MEMORY_DIR
        from hq.outbox import OUTBOX_DIR
        self.outbox_dir = outbox_dir or OUTBOX_DIR   # Client Relations' ready-to-publish files
        self._running: set[asyncio.Task] = set()
        self.bulletin: dict[str, list[str]] = {}   # announcements a lead hears with its next task
        self.reported: set[int] = set()   # tasks that already sent the Captain a report
        # task id -> (turns, spend) at the moment the Captain resumed it: the limits start afresh
        self.allowance: dict[int, tuple[int, float]] = {}
        # The Captain's tone filter: "llm" (Haiku, billed to Juno) or "rules" (free, demo).
        self.tone_engine = tone or office().get("tone", {}).get("engine", "llm")
        cast = roster()
        self._wings: dict[str, str] = {k: v["name"] for k, v in cast["wings"].items()}
        self.captain_name: str = cast.get("captain", {}).get("nickname") or "the Captain"
        self.agents: dict[str, Agent] = {e["id"]: Agent(self, e) for e in cast["agents"]}
        self.held = False   # the whole office paused by the Captain (see hold / release)
        for p in self.store.pauses():   # a pause outlives a restart: only the Captain unpauses
            if p["agent"] == OFFICE:
                self.held = True
                continue
            agent = self.agents.get(p["agent"])
            if agent is not None:
                agent.gate.clear()
                agent.status, agent.paused_by = "paused", p["by"]

    def api_available(self) -> bool:
        """Whether an agent could run a model call right now (a scripted model always can)."""
        return self._llm is not None or bool(office().get("api", {}).get("enabled", False))

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

    def lead_of(self, wing: str) -> Agent | None:
        return next((a for a in self.agents.values() if a.wing == wing and a.tier == "lead"), None)

    def delegate_of(self, wing: str) -> Agent | None:
        """The wing's delegate: its associate, who is also the wing's head of communication."""
        return next((a for a in self.agents.values()
                     if a.wing == wing and a.tier == "associate"), None)

    def name(self, who: str) -> str:
        return self.captain_name if who == CAPTAIN else self.agents[who].nickname

    def render_task(self, task: dict) -> str:
        text = self._render_task(task)
        agent = self.agents.get(task["assignee"])
        news = self.bulletin.pop(agent.wing, []) if agent and agent.tier == "lead" else []
        if news and task["kind"] != "delegation":
            text = "Office notes since you were last at work:\n" + "\n".join(news) + "\n\n" + text
        return text

    def _render_task(self, task: dict) -> str:
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
        # Finished tasks leave `_running` a moment later (in a callback), so only the unfinished
        # ones count: waiting on a finished task returns at once and would spin here forever.
        while pending := [t for t in self._running if not t.done()]:
            await asyncio.gather(*pending, return_exceptions=True)

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

    def say(self, sender: str, recipients: list[str], text: str, task_id: int | None = None,
            visit: bool = True) -> None:
        """Put a line in the chat and on the floor without delivering it to anyone's inbox: the
        asker already has the answer (comms exchanges), so nothing new should wake a colleague."""
        channel = _channel(sender, recipients, self.agents)
        self.store.add_chat(channel=channel, sender=sender, recipients=recipients, text=text,
                            task_id=task_id)
        away = [r for r in recipients if self.agents[r].wing != self.agents[sender].wing] if visit else []
        if away and channel != "lobby":
            self.bus.publish("move", sender, task_id, to=f"desk:{away[0]}")
        self.bus.publish("chat", sender, task_id, channel=channel, recipients=recipients, text=text)
        if away and channel != "lobby":
            self.bus.publish("move", sender, task_id, to=f"desk:{sender}")

    def _deliver(self, sender: str, recipient: str, text: str, channel: str) -> None:
        if is_delegate(self, recipient) and sender != CAPTAIN and self.comms.handle(
                recipient, sender, text,
                fallback=lambda: self._queue(sender, recipient, text, channel)):
            return   # the delegate answers on its comms desk, beside any job in hand
        self._queue(sender, recipient, text, channel)

    def _queue(self, sender: str, recipient: str, text: str, channel: str) -> None:
        """Hand a message to a colleague's inbox, or start a short task for them if idle."""
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
        if task_id is not None:
            self.reported.add(task_id)
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
                    "deliverable, any deadline). If it applies to several wings, assign them all in one "
                    "call (a list of leads, or \"all_leads\"). If it is a question for you, check "
                    "read_office and answer it yourself with report_to_captain. "
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
            self._hold_pass(agent_id)
            return {"routed_to": agent_id, "task_id": agent.current_task, "delivered": "inbox"}
        task_id = self.store.create_task(assignee=agent_id, assigned_by=CAPTAIN,
                                         kind="assignment", title=_title(text), body=text)
        self.store.add_chat(channel=f"dm:{CAPTAIN}|{agent_id}", sender=CAPTAIN,
                            recipients=[agent_id], text=text, task_id=task_id, original=kept)
        self.bus.publish("captain_message", agent_id, task_id, to=agent_id, text=text,
                         delivered="task", original=kept)
        self.bus.publish("task_created", agent_id, task_id, title=_title(text), kind="assignment",
                         assigned_by=CAPTAIN)
        if self.held:   # this task is the conversation itself: his answer ends it
            agent.hold_chat_task = task_id
        self._hold_pass(agent_id)
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
            if agent.tier == "lead" and agent.wing != "audit":
                # A lead is not asked to reply: its delegate speaks for the wing. It still hears
                # the announcement (now if it is working, else with its next assignment).
                fyi = (f"[Announcement from {who} to the whole office]: {text}\n"
                       "(Your delegate replies for the wing; you need not reply.)")
                if agent.current_task is not None or agent.desk.locked():
                    agent.inbox.append(fyi)
                else:
                    self.bulletin.setdefault(agent.wing, []).append(fyi)
                continue
            if is_delegate(self, agent.id) and agent.wing != "audit" and self.comms.handle(
                    agent.id, sender, note):
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
        # Tally's code checks run on every card before it reaches the Captain (free).
        from hq import audit

        agent = self.agents.get(agent_id)
        found = []
        if agent is not None and not agent.is_audit:
            try:   # a failed check must never stop a card reaching the Captain
                found = audit.check_approval(self, agent_id, kind=kind, title=title,
                                             summary=summary, payload=payload)
            except Exception:
                log.exception("audit check failed for approval %r", title)
        payload = {**payload, "audit": [{"rule": f.rule, "severity": f.severity,
                                         "subject": f.subject, "detail": f.detail,
                                         "status": "open"} for f in found]}
        approval_id = self.store.add_approval(kind=kind, agent=agent_id, task_id=task_id,
                                              title=title, summary=summary, payload=payload)
        try:
            # The card's own findings carry its number, so a re-filed card is checked afresh.
            mine = f'approval card "{title}"'
            found = [audit.Finding(f.rule, f.severity, f"approval #{approval_id} \"{title}\"", f.detail)
                     if f.subject == mine else f for f in found]
            ids = self.record_findings(agent_id, self.store.task(task_id) if task_id else None,
                                       found, with_ids=True)
            for entry, f, fid in zip(payload["audit"], found, ids, strict=True):
                entry["subject"], entry["finding"] = f.subject, fid
                entry["status"] = self.store.finding(fid)["status"]
            if found:
                self.store.set_approval_payload(approval_id, payload)
        except Exception:
            log.exception("recording audit findings failed for approval %s", approval_id)
        if kind == "model":
            self.store.set_model_status(payload["ticker"], payload["version"], "awaiting",
                                        approval_id=approval_id)
        self.bus.publish("approval_requested", agent_id, task_id, approval=approval_id, kind=kind,
                         title=title)
        return approval_id

    def decide(self, approval_id: int, decision: str, note: str | None = None, *,
               prices: dict | None = None) -> dict:
        """The Captain's decision on an approval card, sent back to the agent who asked."""
        if decision not in ("approved", "changes", "rejected"):
            raise ValueError("decision must be approved, changes or rejected")
        card = self.store.approval(approval_id)
        if card["status"] != "pending":
            raise ValueError(f"approval {approval_id} is already {card['status']}")
        p = card["payload"]
        if card["kind"] == "model" and decision == "approved":
            m = self.store.model(p.get("ticker", ""), p.get("version", 0))
            newest = self.store.approved_model(p.get("ticker", ""))
            if m and m["approval_id"] != approval_id:
                raise ValueError("This card is stale: that model version was re-filed since. "
                                 "Decide the newer card instead.")
            if newest and m and newest["version"] > m["version"]:
                raise ValueError(f"v{newest['version']} is already the official model; approving "
                                 f"v{m['version']} would roll it back. Decline this card instead.")
        if card["kind"] == "handoff" and decision == "approved":
            from hq import handoff

            if not handoff.readiness_holds(self, p["ticker"], p["version"]):
                raise ValueError("This hand-off is out of date: the approved model or its brief changed "
                                 "since the card was filed. Decline it; a fresh card follows when ready.")
        self._outbox_decided(card, decision)   # may refuse an approval (ValueError) before it is recorded
        self._portfolio_decided(card, decision, prices)
        self.store.decide_approval(approval_id, decision, note)
        mdl = self.store.model(p.get("ticker", ""), p.get("version", 0)) if card["kind"] == "model" else None
        if mdl and mdl["status"] == "awaiting" and mdl["approval_id"] == approval_id:
            self.store.set_model_status(p["ticker"], p["version"], decision)
            self.bus.publish("model_status", card["agent"], card["task_id"], ticker=p["ticker"],
                             version=p["version"], status=decision)
        verb = {"approved": "approved", "changes": "asked for changes on",
                "rejected": "declined"}[decision]
        text = f'{self.captain_name} {verb} your request "{card["title"]}" (approval #{approval_id}).'
        if note:
            text += f" Note: {note}"
        if decision == "approved" and card["kind"] == "model":
            text += (" This version is now the firm's official numbers; distribute it to the "
                     "teams covering this equity.")
        self.bus.publish("approval_decided", card["agent"], card["task_id"], approval=approval_id,
                         decision=decision, note=note, title=card["title"], kind=card["kind"])
        if card["kind"] == "handoff" or (card["kind"] == "model" and decision == "approved"):
            from hq import handoff

            if card["kind"] == "handoff":
                handoff.decided(self, card, decision)
            else:
                handoff.maybe_offer(self, p.get("ticker", ""))
        if card["kind"] == "final_audit" or (card["kind"] in ("newsletter", "deliverable") and decision == "approved"):
            from hq import finalaudit

            if card["kind"] == "final_audit":
                finalaudit.decided(self, card, decision)
            else:
                finalaudit.offer_for_card(self, card)
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

    # watchlist -----------------------------------------------------------------------------
    def add_watch(self, *, ticker: str, added_by: str, source: str, thesis: str,
                  pitch: str | None, price: float | None, spy: float | None,
                  task_id: int | None = None) -> int:
        watch_id = self.store.add_watch(ticker=ticker, added_by=added_by, source=source,
                                        thesis=thesis, pitch=pitch, price=price, spy=spy)
        self.bus.publish("watchlist_added", added_by, task_id, ticker=ticker, source=source,
                         watch=watch_id)
        return watch_id

    def watchlist_view(self, prices: dict[str, float | None]) -> list[dict]:
        """Watchlist rows with the scorecard: return since added vs SPY over the same days."""
        rows = []
        spy_now = prices.get("SPY")
        for w in self.store.watchlist():
            now = prices.get(w["ticker"])
            ret = (now / w["price_at_add"] - 1) if now and w["price_at_add"] else None
            spy_ret = (spy_now / w["spy_at_add"] - 1) if spy_now and w["spy_at_add"] else None
            rows.append({**w, "price_now": now, "return": ret, "spy_return": spy_ret,
                         "vs_spy": (ret - spy_ret) if ret is not None and spy_ret is not None else None,
                         "added_by_name": self.name(w["added_by"]) if w["added_by"] in self.agents else w["added_by"]})
        return rows

    def send_watch_to_research(self, watch_id: int) -> dict:
        """The Captain's 'Send to research': routed through Juno like any office message."""
        w = self.store.watch(watch_id)
        pitch = (f" {self.name('screen_lead')}'s pitch is in coverage/{w['ticker']}/pitch.md."
                 if w["pitch"] else "")
        text = (f"Please start research on {w['ticker']} from the watchlist ({w['source']}). "
                f"Screening's thesis: {w['thesis']}.{pitch}")
        out = self.captain_send("office", text)
        self.store.set_watch_status(watch_id, "researching")
        self.bus.publish("watchlist_status", None, None, watch=watch_id, status="researching")
        return out

    def weekly_reminder(self, now=None) -> bool:
        """Monday nudge from Juno that a fresh screen is available. Plain code, no API call.
        Returns True if a reminder was posted (once per ISO week)."""
        from datetime import datetime
        now = now or datetime.now(self.ledger.tz)
        if now.weekday() != 0 or now.hour < 8:
            return False
        week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        if any(e["payload"].get("week") == week for e in self.store.events_of_type("weekly_reminder")):
            return False
        screen = " and ".join(self.name(i) for i in ("screen_lead", "screen_associate") if i in self.agents)
        cr = " and ".join(self.name(i) for i in ("cr_lead", "cr_associate") if i in self.agents)
        text = ("Good morning. It's Monday, so a fresh Gems or Core screen is ready whenever you want "
                f"it. Pulling the data is free; {screen or 'Screening'} reviewing the results and writing "
                f"pitches costs a little. {cr or 'Client Relations'} can also draft this week's newsletter "
                "from what is approved and on the watchlist. Just say the word and I'll route it.")
        self.store.add_chat(channel=f"dm:{CAPTAIN}|chief_of_staff", sender="chief_of_staff",
                            recipients=[CAPTAIN], text=text)
        self.bus.publish("captain_report", "chief_of_staff", None, text=text)
        self.bus.publish("weekly_reminder", None, None, week=week)
        return True

    # paper portfolio -----------------------------------------------------------------------
    def _prices(self, prices: dict | None, extra: list[str] | None = None) -> dict:
        from hq import portfolio, quotes

        names = sorted(set(portfolio.tickers(self)) | set(extra or []))
        if prices is not None and all(n in prices for n in names):
            return prices
        return quotes.latest(names)

    def _pending_portfolio(self, action: str, ticker: str) -> dict | None:
        return next((c for c in self.store.approvals("pending")
                     if c["kind"] == "portfolio" and c["payload"].get("action") == action
                     and c["payload"].get("ticker") == ticker), None)

    def propose_position(self, agent_id: str, ticker: str, size_pct: int, thesis: str, *,
                         task_id: int | None, prices: dict | None = None) -> int:
        """Put an entry on the Captain's desk, if the rules allow it (ValueError says why not)."""
        from hq import portfolio

        prices = self._prices(prices, [ticker])
        ok = portfolio.check_entry(self, ticker, size_pct, prices)
        pending = self._pending_portfolio("enter", ticker)
        if pending:
            raise ValueError(f"An entry for {ticker} is already waiting for a decision (approval #{pending['id']}).")
        m = ok["model"]
        pts = m["summary"].get("price_targets", {})
        summary = (f"Enter {ticker} at {size_pct}% of the paper portfolio: about ${ok['amount']:,.2f} at the "
                   f"latest price of ${ok['price']:,.2f}. Quant model v{m['version']} rates it Outperform with a "
                   f"base price target of ${pts.get('base', 0):,.2f} (bear ${pts.get('bear', 0):,.2f}, bull "
                   f"${pts.get('bull', 0):,.2f}).\n\n{thesis}\n\nA paper fill at the price when you "
                   "approve, which may differ from the price above. No real money moves.")
        return self.request_approval(agent_id, kind="portfolio", title=f"Enter {ticker} at {size_pct}%",
                                     summary=summary, task_id=task_id,
                                     payload={"action": "enter", "ticker": ticker, "size_pct": size_pct,
                                              "version": m["version"], "thesis": thesis, "attachments": []})

    def propose_exit(self, agent_id: str, ticker: str, reason: str, *, task_id: int | None,
                     prices: dict | None = None, code: str | None = None) -> int:
        from hq import portfolio

        position = next((p for p in self.store.positions("open") if p["ticker"] == ticker), None)
        if position is None:
            raise ValueError(f"The portfolio holds no open {ticker} position.")
        pending = self._pending_portfolio("exit", ticker)
        if pending:
            raise ValueError(f"An exit for {ticker} is already waiting for a decision (approval #{pending['id']}).")
        prices = self._prices(prices, [ticker])
        row = next(r for r in portfolio.scoreboard(self, prices)["open"] if r["id"] == position["id"])
        pct = lambda x: "n/a" if x is None else f"{x * 100:+.1f}%"
        what = portfolio.FLAGS[portfolio.flag_kind(code)] if code else ""
        lead = (f"Exit flag from the office's code checks (no API cost): {ticker} {what}. "
                if code else f"Exit {ticker}. ")
        now = (f"now ${row['price']:,.2f}: {pct(row['return'])}, {pct(row['vs_spy'])} against the S&P 500"
               if row["price"] else "no quote right now")
        summary = (f"{lead}{reason}\n\nEntered {row['entry_day']} at ${row['entry_price']:,.2f} "
                   f"({row['size_pct']}%); {now}.\n\nApprove to close on paper at the latest price, or "
                   "decline to hold"
                   + (" (this flag is then not raised again unless the facts change)." if code else "."))
        return self.request_approval(agent_id, kind="portfolio",
                                     title=f"Exit {ticker}" + (f": {what}" if code else ""),
                                     summary=summary, task_id=task_id,
                                     payload={"action": "exit", "ticker": ticker, "position": position["id"],
                                              "code": code, "reason": reason, "attachments": []})

    def _portfolio_decided(self, card: dict, decision: str, prices: dict | None) -> None:
        """Carry out the Captain's decision on a portfolio card, before the card is closed. An
        approval that can't be filled (the rating changed, no quote, not enough cash) is refused
        with the reason and the card stays open."""
        from hq import portfolio

        p = card["payload"]
        if card["kind"] != "portfolio" or p.get("action") not in ("enter", "exit"):
            return
        if p["action"] == "enter" and decision == "approved":
            portfolio.enter(self, ticker=p["ticker"], size_pct=p["size_pct"], thesis=p.get("thesis", ""),
                            proposed_by=card["agent"], approval_id=card["id"],
                            prices=self._prices(prices, [p["ticker"]]))
        elif p["action"] == "exit" and decision == "approved":
            portfolio.close(self, p["position"], reason=p.get("reason", ""), approval_id=card["id"],
                            prices=self._prices(prices, [p["ticker"]]))
        elif p["action"] == "exit" and p.get("code") and decision == "rejected":
            self.store.hold_flag(p["position"], p["code"])   # held: don't raise this same flag again
        if decision == "approved":
            self.bus.publish("portfolio_changed", card["agent"], card["task_id"], action=p["action"],
                             ticker=p["ticker"])

    def portfolio_tick(self, prices: dict | None = None) -> dict:
        """Mark to market and raise exit cards. Plain code on free prices: no API call."""
        from hq import portfolio

        if not self.store.positions():
            return {"marked": False, "flags": []}
        prices = self._prices(prices)
        marked = portfolio.mark(self, prices) is not None
        raised = []
        filer = OFFICE   # raised by code: no colleague is asked to act, so no model call follows
        for flag in portfolio.exit_flags(self, prices):
            ticker = flag["position"]["ticker"]
            if self._pending_portfolio("exit", ticker):
                continue
            raised.append(self.propose_exit(filer, ticker, flag["detail"], task_id=None, prices=prices,
                                            code=flag["code"]))
        return {"marked": marked, "flags": raised}

    def portfolio_view(self, prices: dict | None = None) -> dict:
        from hq import portfolio

        s = portfolio.scoreboard(self, self._prices(prices))
        pending = [{"id": c["id"], "title": c["title"], "action": c["payload"].get("action"),
                    "ticker": c["payload"].get("ticker")} for c in self.store.approvals("pending")
                   if c["kind"] == "portfolio"]
        return {**s, "summary": portfolio.summary_text(s), "history": portfolio.history(self),
                "pending": pending}

    # outbox --------------------------------------------------------------------------------
    def _outbox_decided(self, card: dict, decision: str) -> None:
        """Carry the Captain's decision into the Outbox, before the card itself is closed.
        Approval hands the issue to the publisher (today: marks the files ready to paste), and
        is refused with a reason if the issue can no longer go out as it stands. Any other
        decision is best effort: a missing Outbox entry must not stop him declining a card."""
        from hq import outbox
        from hq.publish import get_publisher

        p = card["payload"]
        issue, memo = p.get("issue"), p.get("deliverable")
        if card["kind"] == "deck":
            self._deck_decided(card, decision)
        if card["kind"] in ("newsletter", "deck") and issue:
            if decision == "approved":
                try:
                    errors = [e for e in outbox.recheck(self, issue) if e["level"] == "error"]
                except (KeyError, OSError) as e:
                    raise ValueError(f"The issue's files are missing from the Outbox ({e}). Ask "
                                     "for it to be finalized again.") from e
                if errors:
                    raise ValueError("What is approved changed since this issue was finalized: "
                                     + "; ".join(e["msg"] for e in errors[:3])
                                     + " Request changes so it is revised against the current model.")
                try:
                    publisher = get_publisher(p.get("publisher"))
                except ValueError:
                    publisher = get_publisher("outbox")   # a renamed publisher must not strand the issue
                publisher.publish(self, issue)
            else:
                try:
                    outbox.set_status(self.outbox_dir, issue, decision)
                except (KeyError, OSError):
                    log.exception("could not update outbox issue %s", issue)
            self.bus.publish("outbox_status", card["agent"], card["task_id"], issue=issue, status=decision)
        elif card["kind"] == "outreach" and p.get("email"):
            self._outreach_decided(card, decision)
        elif card["kind"] == "deliverable" and memo:
            try:
                outbox.set_deliverable_status(self.outbox_dir, memo, decision)
            except (KeyError, OSError) as e:
                if decision == "approved":
                    raise ValueError(f"The client file is missing from the Outbox ({e}). Ask for "
                                     "the memo to be packaged again.") from e
                log.exception("could not update outbox deliverable %s", memo)
            self.bus.publish("outbox_status", card["agent"], card["task_id"], deliverable=memo,
                             status=decision)

    def _deck_decided(self, card: dict, decision: str) -> None:
        """Approval re-runs the deck's gate and its matching newsletter's against today's facts and is refused with
        the reason if either no longer holds; the package is then marked ready (nothing is sent). Any other
        decision is best effort."""
        from hq import deckpack, outbox

        p = card["payload"]
        deck_id = p.get("deck")
        if decision == "approved":
            problems = deckpack.recheck(self, deck_id) if deck_id else ["the deck is missing from the Outbox"]
            if p.get("issue"):
                try:
                    problems += [e["msg"] for e in outbox.recheck(self, p["issue"]) if e["level"] == "error"]
                except (KeyError, OSError):
                    problems.append("the matching newsletter's files are missing")
            if problems:
                raise ValueError("What is approved changed since this deck was finalized: " + "; ".join(problems[:3])
                                 + ". Request changes so it is revised against the current facts.")
        try:
            deckpack.set_status(self, deck_id, decision)
        except (KeyError, OSError):
            if decision == "approved":
                raise ValueError("The deck's files are missing from the Outbox. Ask for it to be finalized again.") from None
            log.exception("could not update outbox deck %s", deck_id)
        self.bus.publish("outbox_status", card["agent"], card["task_id"], deck=deck_id, status=decision)

    def _outreach_decided(self, card: dict, decision: str) -> None:
        """Approval re-runs the code gate against today's facts (an address may have opted out
        since the card was filed) and only then hands the email to the mailer. Anything else
        is best effort."""
        from hq import outreach
        from hq.publish import get_mailer

        email_id = card["payload"]["email"]
        if decision == "approved":
            try:
                errors = [e for e in outreach.recheck(self, email_id) if e["level"] == "error"]
            except (KeyError, OSError) as ex:
                raise ValueError(f"The email's files are missing from the Outbox ({ex}). Ask for it "
                                 "to be finalized again.") from ex
            if errors:
                raise ValueError("This email can no longer go out: " + "; ".join(e["msg"] for e in errors[:3])
                                 + " Decline it or request changes.")
            try:
                mailer = get_mailer(card["payload"].get("mailer"))
            except ValueError:
                mailer = get_mailer("draft")   # a renamed mailer must not strand the email
            mailer.deliver(self, email_id)
        else:
            try:
                outreach.set_status(self, email_id, decision)
            except (KeyError, OSError):
                log.exception("could not update outreach email %s", email_id)
        self.bus.publish("outreach_status", card["agent"], card["task_id"], email=email_id, status=decision)

    def outbox_view(self) -> dict:
        from hq import outbox

        who = lambda a: self.name(a) if a in self.agents else a
        issues = [{**m, "drafted_by_name": who(m.get("drafted_by")),
                   "revised_by_name": who(m.get("revised_by"))} for m in outbox.issues(self.outbox_dir)]
        from hq import outreach

        mails = [{**m, "drafted_by_name": who(m.get("drafted_by"))} for m in outreach.emails(self)]
        from hq import deckpack

        return {"issues": issues, "deliverables": outbox.deliverables(self.outbox_dir), "outreach": mails,
                "decks": deckpack.decks(self),
                "outreach_from": outreach.settings()["from_address"],
                "publisher": outbox.settings()["publisher"]}

    def model_lock(self, ticker: str) -> asyncio.Lock:
        return self._model_locks.setdefault(ticker, asyncio.Lock())

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
        for mdl in self.store.models():
            if mdl["created_by"] == agent_id:
                s = mdl["summary"]
                pts = s.get("price_targets", {})
                docs.append({"ts": mdl["created"], "kind": "workbook", "source": "model",
                             "title": f"{mdl['ticker']} model v{mdl['version']} ({mdl['status']})",
                             "text": (f"Bear ${pts.get('bear', 0):,.2f} / base ${pts.get('base', 0):,.2f} / "
                                      f"bull ${pts.get('bull', 0):,.2f} · {s.get('rating')} · formula "
                                      f"check {'passed' if s.get('check', {}).get('ok') else 'FAILED'}"),
                             "ticker": mdl["ticker"], "version": mdl["version"], "status": mdl["status"],
                             **({"file": f"/files/quant/{mdl['ticker']}/{Path(mdl['path']).name}"}
                                if Path(mdl["path"]).is_file() else {})})
        return sorted(docs, key=lambda d: d["ts"], reverse=True)

    def resolve_incident(self, incident_id: int) -> None:
        self.store.resolve_incident(incident_id)
        self.bus.publish("incident_resolved", None, None, incident=incident_id)

    # audit --------------------------------------------------------------------------------
    def audit_task(self, task: dict) -> list[dict]:
        """Tally's code checks on a finished assignment (free). Audit's own work is not
        re-audited, and delegated jobs are covered through the lead's assignment."""
        from hq import audit, finalaudit

        agent = self.agents.get(task["assignee"])
        if task["kind"] != "assignment" or agent is None or agent.is_audit:
            return []
        from hq import handoff

        if task["title"].startswith(finalaudit.REDO_PREFIX):
            finalaudit.after_redo(self, task)   # a targeted redo ended: close what now ties out

        handoff.scan(self)   # a finished report may complete a hand-off (free; files a card at most)
        return self.record_findings(agent.id, task, audit.check_task(self, task))

    def record_findings(self, agent_id: str | None, task: dict | None, findings: list, *,
                        with_ids: bool = False) -> list:
        """Save new findings, announce them, and call Vera in on the flags. Returns the new
        findings, or with `with_ids` the finding id for every input (new or already known)."""
        new, ids = [], []
        for f in findings:
            fid, is_new = self.store.add_finding(
                agent=agent_id, task_id=task["id"] if task else None,
                root_id=task["root_id"] if task else None, rule=f.rule, severity=f.severity,
                subject=f.subject, detail=f.detail)
            ids.append(fid)
            if not is_new:
                continue   # already recorded for this piece of work
            self.bus.publish("audit_flag", agent_id, task["id"] if task else None, finding=fid,
                             rule=f.rule, severity=f.severity, subject=f.subject, detail=f.detail)
            new.append(self.store.finding(fid))
        flags = [f for f in new if f["severity"] == "flag"]
        if flags:
            self.send_to_vera(flags)
        return ids if with_ids else new

    def send_to_vera(self, flags: list[dict]) -> int | None:
        """Give Vera one review task for a batch of flags. With the API off the flags simply
        stay open in the Audit tab, where the Captain can dismiss them or send them later."""
        vera = "audit_lead"
        if vera not in self.agents or not self.api_available():
            return None
        whose = sorted({self.name(f["agent"]) for f in flags if f["agent"] in self.agents})
        lines = "\n".join(f"- [finding #{f['id']}] {f['rule']} · {f['subject']}: {f['detail']}"
                          for f in flags)
        body = (f"Tally's code checks flagged work by {', '.join(whose) or 'a colleague'}:\n\n"
                f"{lines}\n\n"
                "Review each flag against the evidence (audit_log, read_file, get_model). Close "
                "each one with resolve_finding: cleared if the check was wrong, upheld if it "
                "stands. For an upheld flag, tell the colleague exactly what to fix with "
                "send_message. Pause someone only if the problem is serious or keeps happening. "
                "End with a one-line recap.")
        n = len(flags)
        by = "audit_associate" if "audit_associate" in self.agents else "office"
        task_id = self.assign(vera, body, by=by,
                              title=f"Review {n} flag{'s' if n != 1 else ''} on "
                                    f"{', '.join(whose) or 'recent'} work")
        for f in flags:
            self.store.set_finding(f["id"], "reviewing", review_task_id=task_id)
        self.bus.publish("audit_review", vera, task_id, findings=[f["id"] for f in flags])
        return task_id

    def review_finding(self, finding_id: int) -> int:
        """The Captain's 'Ask Vera': send one open finding for review."""
        f = self.store.finding(finding_id)
        if f["status"] != "open":
            raise ValueError(f"finding {finding_id} is already {f['status']}")
        if "audit_lead" not in self.agents:
            raise ValueError("There is no Audit lead in the roster to review this.")
        task_id = self.send_to_vera([f])
        if task_id is None:
            raise ValueError("The API is switched off, so Audit can't review this now. It stays "
                             "open here; dismiss it or send it once the API is on.")
        return task_id

    def reopen_unreviewed(self, review_task_id: int) -> None:
        """A review task ended (finished, failed or paused) without ruling on everything: those
        findings go back to open, so they count as waiting and can be sent or dismissed."""
        if self.store.reopen_findings(review_task_id):
            self.bus.publish("audit_review", "audit_lead", review_task_id, findings=[], reopened=True)

    def resolve_finding(self, finding_id: int, verdict: str, *, by: str,
                        note: str | None = None) -> dict:
        """Close a finding: Vera clears or upholds it, the Captain can dismiss it."""
        allowed = ("dismissed",) if by == CAPTAIN else ("cleared", "upheld")
        if verdict not in allowed:
            raise ValueError(f"verdict must be {' or '.join(allowed)}")
        f = self.store.finding(finding_id)
        if f["status"] not in ("open", "reviewing"):
            raise ValueError(f"finding {finding_id} is already {f['status']}")
        self.store.set_finding(finding_id, verdict, by=by, note=note)
        for card in self.store.approvals():   # keep the card's audit line in step with the ruling
            entries = card["payload"].get("audit") or []
            if any(e.get("finding") == finding_id for e in entries):
                for e in entries:
                    if e.get("finding") == finding_id:
                        e["status"] = verdict
                self.store.set_approval_payload(card["id"], card["payload"])
        self.bus.publish("audit_resolved", f["agent"], f["task_id"], finding=finding_id,
                         verdict=verdict, by=by, note=note, rule=f["rule"], subject=f["subject"])
        return self.store.finding(finding_id)

    # memory -------------------------------------------------------------------------------
    def memory_write(self, agent_id: str, kind: str, text: str,
                     task_id: int | None = None) -> dict:
        """An agent saving to office memory. A clean desk note is saved at once; a flagged note
        and every wiki proposal wait for the Captain."""
        from hq import audit
        from hq.memory import add_desk_note

        text = " ".join(text.split())   # one line: a note is one entry in the file
        reasons = audit.screen_note(text)
        if kind == "desk" and not reasons:
            add_desk_note(agent_id, text, self.memory_dir)
            write_id = self.store.add_memory_write(agent=agent_id, task_id=task_id, kind=kind,
                                                   text=text, reasons=[], status="saved")
            self.bus.publish("memory_saved", agent_id, task_id, write=write_id, kind=kind, text=text)
            return {"id": write_id, "held": False, "reasons": []}
        write_id = self.store.add_memory_write(agent=agent_id, task_id=task_id, kind=kind,
                                               text=text, reasons=reasons, status="pending")
        self.bus.publish("memory_held", agent_id, task_id, write=write_id, kind=kind, text=text,
                         reasons=reasons)
        return {"id": write_id, "held": True, "reasons": reasons}

    def decide_memory(self, write_id: int, decision: str) -> dict:
        """The Captain's call on a held note or a wiki proposal."""
        from hq.memory import add_desk_note, add_wiki_entry

        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        w = self.store.memory_write(write_id)
        if w["status"] != "pending":
            raise ValueError(f"memory write {write_id} is already {w['status']}")
        fits = True
        if decision == "approved":
            if w["kind"] == "wiki":
                fits = add_wiki_entry(w["text"], self.memory_dir)
            else:
                add_desk_note(w["agent"], w["text"], self.memory_dir)
        self.store.set_memory_status(write_id, decision)
        self.bus.publish("memory_decided", w["agent"], w["task_id"], write=write_id,
                         kind=w["kind"], decision=decision)
        return {**self.store.memory_write(write_id), "fits": fits}

    def remove_memory(self, write_id: int) -> dict:
        """The Captain deleting something already in memory (a saved note or a wiki entry)."""
        from hq.memory import remove_note, remove_wiki_entry

        w = self.store.memory_write(write_id)
        if w["status"] not in ("saved", "approved"):
            raise ValueError(f"memory write {write_id} is {w['status']}; nothing to remove")
        if w["kind"] == "wiki":
            path = self.memory_dir / "wiki.md"
            gone = remove_wiki_entry(w["text"], self.memory_dir)
        else:
            path = self.memory_dir / "desks" / f"{w['agent']}.md"
            gone = remove_note(w["agent"], w["text"], self.memory_dir)
        if not gone and path.is_file() and w["text"] in path.read_text():
            raise ValueError(f"Couldn't remove it automatically; delete the line by hand in {path}.")
        self.store.set_memory_status(write_id, "removed")   # deleted now, or already gone
        self.bus.publish("memory_decided", w["agent"], w["task_id"], write=write_id,
                         kind=w["kind"], decision="removed")
        return self.store.memory_write(write_id)

    # the daily audit digest -----------------------------------------------------------------
    def digest(self, day: str | None = None) -> dict:
        """One day's digest, computed fresh from the ledger and logs (no API call)."""
        from hq import audit

        d = audit.build_digest(self, day or self.ledger.today())
        return {**d, "text": audit.render_digest(d)}

    def post_digest(self, day: str) -> bool:
        """Post a day's digest to the Captain under Tally's name, once, if anything happened."""
        if self.store.digest(day) is not None:
            return False
        d = self.digest(day)
        if not d["active"]:
            return False
        text = d.pop("text")
        self.store.save_digest(day, text, d)
        tally = "audit_associate"
        if tally in self.agents:
            self.store.add_chat(channel=f"dm:{CAPTAIN}|{tally}", sender=tally,
                                recipients=[CAPTAIN], text=text)
            self.bus.publish("captain_report", tally, None, text=text)
        self.bus.publish("audit_digest", tally if tally in self.agents else None, None, day=day)
        return True

    def daily_digest(self, now=None, days_back: int = 7) -> list[str]:
        """Post the digest for each finished day that had work and has none yet."""
        from datetime import datetime, timedelta
        today = (now or datetime.now(self.ledger.tz)).date()
        posted = []
        for back in range(days_back, 0, -1):
            day = (today - timedelta(days=back)).isoformat()
            if self.post_digest(day):
                posted.append(day)
        return posted

    def audit_view(self) -> dict:
        """Everything the Audit tab shows."""
        from hq import finalaudit

        who = lambda a: self.name(a) if a in self.agents or a == CAPTAIN else (a or "Office")
        findings = [{**f, "name": who(f["agent"]),
                     "resolved_by_name": who(f["resolved_by"]) if f["resolved_by"] else None}
                    for f in self.store.findings(limit=120)]
        memory = [{**w, "name": who(w["agent"])} for w in self.store.memory_writes(limit=120)]
        return {"findings": findings, "memory": memory, "digest": self.digest(),
                "digests": [{"day": d["day"], "ts": d["ts"], "text": d["text"]}
                            for d in self.store.digests()],
                "paused": [{**p, "name": who(p["agent"]), "by_name": who(p["by"])}
                           for p in self.store.pauses() if p["agent"] in self.agents],
                "final": finalaudit.view(self),
                "api_available": self.api_available()}

    def audit_counts(self) -> dict:
        return {"open_flags": self.store.count_findings("open", "flag"),
                "memory_pending": self.store.count_memory("pending")}

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
        agent.paused_by = by
        self.store.set_pause(agent.id, by, reason)
        agent.set_status("paused", by=by, reason=reason)
        if by != CAPTAIN:
            self.raise_incident(agent.id, agent.current_task, "paused",
                                f"Paused by {self.name(by)}: {reason}", by=by)

    def resume(self, agent_id: str, *, by: str) -> None:
        if by != CAPTAIN:
            raise PermissionError("Only the Captain can unpause an agent.")
        agent = self.agents[self.resolve(agent_id)]
        agent.gate.set()
        agent.paused_by = None
        self.store.clear_pause(agent.id)
        agent.set_status("working" if agent.current_task else "idle", by=by)

    def hold(self) -> None:
        """The Captain pauses the whole office so he can read and talk one to one. Model calls
        already in flight finish; after that nobody takes a step except to answer a message he
        sends them directly. Nothing is lost: every task waits where it is."""
        if self.held:
            return
        self.held = True
        self.store.set_pause(OFFICE, CAPTAIN, "office paused")
        self.bus.publish("office_hold", None, None, held=True)

    def release(self) -> None:
        if not self.held:
            return
        self.held = False
        self.store.clear_pause(OFFICE)
        for agent in self.agents.values():
            agent.hold_passes = 0
            agent.hold_wake.set()
        self.bus.publish("office_hold", None, None, held=False)

    def _hold_pass(self, agent_id: str) -> None:
        """While the office is paused, a direct message from the Captain earns that agent one turn."""
        if self.held:
            agent = self.agents[agent_id]
            agent.hold_passes += 1
            agent.hold_followups = 0
            agent.hold_wake.set()

    def on_budget_exhausted(self, detail: str) -> None:
        if not self.clocked_out:
            self.clocked_out = True
            self.bus.publish("office_status", None, None, status="clocked_out", detail=detail)

    # stuck work -------------------------------------------------------------------------
    RESUMABLE = ("turn_cap", "tool_loop", "task_cost_cap", "api_off", "api_credit", "api_auth",
                 "api_rate", "api_busy", "cancelled")

    def api_problem(self, agent: str, task_id: int, kind: str, detail: str) -> None:
        """The Anthropic account or service refused a call. One incident per kind covers every
        task it stops; resuming from that incident restarts them all."""
        if not any(i["kind"] == kind for i in self.store.incidents()):
            self.raise_incident(agent, task_id, kind, detail)

    def resume_task(self, task_id: int) -> int:
        """The Captain restarts a task the guards or an API problem paused. It carries on from
        where it stopped, with a fresh allowance of turns and spend."""
        t = self.store.task(task_id)
        if t is None:
            raise KeyError(f"No task {task_id}.")
        if t["status"] != "paused":
            raise ValueError(f"Task {task_id} is {t['status']}, not paused.")
        if t["kind"] == "delegation":
            raise ValueError("That was a delegated job; its lead has already moved on. Ask the lead "
                             "to hand it over again.")
        reason = (t["status_reason"] or "").split(":")[0]
        if reason == "context_cap":
            raise ValueError("That conversation grew too long to continue. Give the work again as a "
                             "new assignment.")
        if reason not in self.RESUMABLE:
            raise ValueError(f"Task {task_id} can't be resumed from here ({t['status_reason']}).")
        if not self.api_available():
            raise ValueError("The API is switched off (api.enabled), so nothing can run yet.")
        saved = self.store.conversation(task_id)
        turns = sum(1 for m in saved[1] if m["role"] == "assistant") if saved else 0
        self.allowance[task_id] = (turns, self.store.spend_for_root(t["root_id"]))
        self.store.set_task_status(task_id, "queued", reason="resumed by the Captain")
        self.bus.publish("task_resumed", t["assignee"], task_id, title=t["title"])
        self._schedule(t["assignee"], task_id)
        return task_id

    def resume_from_incident(self, incident_id: int) -> list[int]:
        """Restart what an incident stopped, then close it. An API incident restarts every task
        it paused; any other restarts its one task."""
        inc = next((i for i in self.store.incidents() if i["id"] == incident_id), None)
        if inc is None:
            raise KeyError(f"No open incident {incident_id}.")
        if inc["kind"].startswith("api_"):
            ids = [t["id"] for t in self.store.tasks("paused")
                   if t["kind"] != "delegation" and (t["status_reason"] or "").startswith(inc["kind"])]
        else:
            ids = [inc["task_id"]] if inc["task_id"] else []
        resumed = [self.resume_task(i) for i in ids]
        if not ids:
            raise ValueError("Nothing is waiting to be resumed for this incident.")
        self.resolve_incident(incident_id)
        return resumed

    def office_report(self) -> dict:
        """A plain snapshot of the office for Juno: who is doing what, what is waiting or stuck,
        and what waits on the Captain. No costs: the ledger is the Captain's."""
        people = []
        for a in self.agents.values():
            task = self.store.task(a.current_task) if a.current_task else None
            people.append({"name": a.nickname, "id": a.id, "role": a.role,
                           "wing": self.wing_name(a.wing), "status": a.status,
                           "working_on": task["title"] if task else None})
        work = [{"task": t["id"], "who": self.name(t["assignee"]), "title": t["title"],
                 "status": t["status"], "why": t["status_reason"]}
                for t in self.store.open_tasks()[-25:]]
        cards = [{"approval": c["id"], "kind": c["kind"], "title": c["title"],
                  "from": self.name(c["agent"]) if c["agent"] in self.agents else c["agent"]}
                 for c in self.store.approvals("pending")]
        problems = [{"incident": i["id"], "kind": i["kind"], "detail": i["detail"]}
                    for i in self.store.incidents()]
        return {"office_paused_by_captain": self.held, "clocked_out_for_the_day": self.clocked_out,
                "colleagues": people, "unfinished_work": work,
                "waiting_on_captain": cards, "open_incidents": problems}

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
            "office": {"clocked_out": self.clocked_out, "held": self.held, "day": day,
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
            "model_registry": self.store.models(),
            "watchlist_count": len(self.store.watchlist()),
            "audit": self.audit_counts(),
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
