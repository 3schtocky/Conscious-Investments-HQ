"""Event bus: every agent action becomes an event for the live office and the log.

Streaming deltas (thinking_delta, text_delta) go only to live subscribers, because there are
thousands of them per task. Every other event is also saved to SQLite, including the completed
thinking and text blocks, so the full record survives without the per-token noise.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from hq.store import Store

EPHEMERAL = {"thinking_delta", "text_delta"}


@dataclass
class Event:
    type: str
    agent: str | None = None
    task_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "type": self.type, "agent": self.agent,
                "task_id": self.task_id, **self.payload}


class EventBus:
    def __init__(self, store: Store, queue_size: int = 5000):
        self.store = store
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._queue_size = queue_size

    def publish(self, type_: str, agent: str | None = None, task_id: int | None = None,
                **payload: Any) -> Event:
        event = Event(type_, agent, task_id, payload)
        if type_ not in EPHEMERAL:
            event.id = self.store.add_event(type_, agent, task_id, payload)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled viewer must never block the office; it drops live deltas.
                pass
        return event

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(self._queue_size)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(q)
