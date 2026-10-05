"""Code-level guardrails. These run no matter what any agent (or Audit) decides.

Two kinds of outcome:
- **Soft block** (`GuardBlock`): a single action is refused and the agent is told why in its
  tool result, so it can change course (e.g. a near-duplicate message, a ping-pong limit).
- **Hard trip** (`GuardTripped`): the task stops and is paused with an incident for the
  Captain (e.g. turn cap, per-task spend cap, a tool-call loop, context overflow).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from difflib import SequenceMatcher

from hq.config import office

LEADERSHIP = {"captain", "chief_of_staff"}   # messages from these reset a ping-pong thread
# After one of these runs the world has changed, so an identical earlier call (rebuilding a model
# after editing its assumptions, re-reading a file just written) is progress, not a loop.
STATE_CHANGING = {"write_file", "draft_assumptions", "erb_facts", "save_newsletter",
                  "finalize_newsletter", "run_screen", "pitch_memo"}


class GuardBlock(Exception):
    """Refuse one action; the agent sees the reason as a tool error."""


class GuardTripped(Exception):
    """Stop the task and pause it for the Captain."""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class TaskGuard:
    """Per-task limits, checked by the agent loop."""

    def __init__(self, *, max_turns: int, cost_cap: float, max_context_tokens: int | None = None,
                 repeat_tool_calls: int | None = None, max_delegations: int | None = None):
        limits = office()["limits"]
        self.max_turns = max_turns
        self.cost_cap = cost_cap
        self.max_context_tokens = max_context_tokens
        self.repeat_tool_calls = repeat_tool_calls or limits["repeat_tool_calls"]
        self.max_delegations = max_delegations or limits["max_delegations_per_task"]
        self.turns = 0
        self.delegations = 0
        self.group_posts = 0
        self.notes = 0
        self._tool_calls: Counter[str] = Counter()

    def before_turn(self, spent_on_task: float) -> None:
        if self.turns >= self.max_turns:
            raise GuardTripped("turn_cap", f"Reached the {self.max_turns}-turn limit for one task.")
        if spent_on_task >= self.cost_cap:
            raise GuardTripped("task_cost_cap",
                               f"Task spend ${spent_on_task:.2f} reached its ${self.cost_cap:.2f} cap.")
        self.turns += 1

    def after_turn(self, context_tokens: int) -> None:
        if self.max_context_tokens and context_tokens >= self.max_context_tokens:
            raise GuardTripped("context_cap",
                               f"Context reached {context_tokens:,} tokens "
                               f"(limit {self.max_context_tokens:,}).")

    def on_tool_call(self, name: str, tool_input: dict) -> None:
        key = name + ":" + json.dumps(tool_input, sort_keys=True, default=str)
        self._tool_calls[key] += 1
        if name in STATE_CHANGING:
            for other in [k for k in self._tool_calls if not k.startswith(name + ":")]:
                del self._tool_calls[other]
        if self._tool_calls[key] >= self.repeat_tool_calls:
            raise GuardTripped("tool_loop",
                               f"Called {name} with identical input {self._tool_calls[key]} times.")

    GROUP_POST_LIMIT = 2

    def check_group_post(self) -> None:
        """Group chats are loud: at most GROUP_POST_LIMIT posts per task, so replies can't
        snowball. Checked before posting; counted only once a post goes through."""
        if self.group_posts >= self.GROUP_POST_LIMIT:
            raise GuardBlock(f"You've already posted {self.GROUP_POST_LIMIT} times in the group "
                             "for this task. Carry on with your work.")

    def record_group_post(self) -> None:
        self.group_posts += 1

    NOTE_LIMIT = 3

    def check_note(self) -> None:
        if self.notes >= self.NOTE_LIMIT:
            raise GuardBlock(f"You've saved {self.NOTE_LIMIT} desk notes this task; that's plenty.")

    def record_note(self) -> None:
        self.notes += 1

    def on_delegate(self) -> None:
        if self.delegations >= self.max_delegations:
            raise GuardBlock(f"Delegation limit reached ({self.max_delegations} per task). "
                             "Finish with what you have or report back.")
        self.delegations += 1


class ConversationGuard:
    """Office-wide limits on agent-to-agent chatter: ping-pong threads and repeated messages."""

    def __init__(self, *, max_exchanges: int | None = None, similarity: float | None = None,
                 history: int = 5):
        limits = office()["limits"]
        self.max_exchanges = max_exchanges or limits["max_agent_exchanges"]
        self.similarity = similarity or limits["repeat_similarity"]
        self._threads: Counter[frozenset[str]] = Counter()
        self._recent: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=history))

    def check_message(self, sender: str, recipients: list[str], text: str) -> None:
        """Call before delivering a message. Raises GuardBlock to refuse it."""
        norm = " ".join(text.lower().split())
        for prev in self._recent[sender]:
            if SequenceMatcher(None, prev, norm).ratio() >= self.similarity:
                raise GuardBlock("This message is nearly identical to one you already sent. "
                                 "Say something new, or wait for a reply.")
        if sender not in LEADERSHIP:
            for r in recipients:
                if r in LEADERSHIP:
                    continue
                pair = frozenset((sender, r))
                if self._threads[pair] >= self.max_exchanges:
                    raise GuardBlock(
                        f"You and {r} have exchanged {self._threads[pair]} messages in a row. "
                        "Bring in the chief_of_staff or wrap up with what you have.")

    def record_message(self, sender: str, recipients: list[str], text: str) -> None:
        self._recent[sender].append(" ".join(text.lower().split()))
        if sender in LEADERSHIP:
            for r in recipients:
                for pair in [p for p in self._threads if r in p]:
                    del self._threads[pair]
            return
        for r in recipients:
            if r not in LEADERSHIP:
                self._threads[frozenset((sender, r))] += 1
