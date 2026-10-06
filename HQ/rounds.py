"""Juno's rounds: she walks the floor, asks each busy wing's delegate where things stand, and
relays the roll-up to the Captain, while the office runs and while it is paused.

The delegates answer from the code-built digest (`HQ.digest`), so no lead is interrupted. While
the office is paused, or the API is off or out of budget, nobody takes a model turn: the roll-up
is the digest read out by code and costs nothing. Juno herself makes no model call on a round.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from HQ.config import office as office_config
from HQ.digest import office_digest

if TYPE_CHECKING:
    from HQ.engine.runtime import Office

log = logging.getLogger(__name__)
JUNO = "chief_of_staff"
QUESTION = ("Quick round: where is your wing right now? One or two plain sentences: what is "
            "moving, what is stuck, and what is waiting on {captain}.")
DEFAULTS = {"enabled": True, "interval_seconds": 120, "heartbeat_ticks": 3, "walk_pause_seconds": 1.2}


def settings() -> dict:
    return {**DEFAULTS, **(office_config().get("rounds") or {})}


def signature(digest: dict) -> str:
    """What changed in the office, ignoring clocks and turn counts that tick every minute."""
    return json.dumps([[w["wing"], w["state"], [p["working_on"] for p in w["people"]],
                        w["blockers"], w["waiting_on_captain"]]
                       for w in digest["wings"] if w["state"] != "idle"], sort_keys=True)


class Rounds:
    def __init__(self, office: Office):
        self.office = office
        self._lock = asyncio.Lock()
        self._last_signature: str | None = None
        self._ticks = 0

    def latest(self) -> dict | None:
        rows = self.office.store.events_where(types=["rounds"], limit=1)
        return {**rows[-1]["payload"], "ts": rows[-1]["ts"]} if rows else None

    async def walk(self, *, reason: str = "asked", post: bool = False) -> dict:
        """One round. `post` also sends the roll-up to the Captain in his chat."""
        async with self._lock:
            return await self._walk(reason, post)

    async def _walk(self, reason: str, post: bool) -> dict:
        o = self.office
        cfg = settings()
        digest = office_digest(o)
        busy = [w for w in digest["wings"] if w["state"] != "idle" and o.delegate_of(w["wing"])]
        mode = "model" if o.comms.can_think() else "code"
        wings = []
        pause = float(cfg["walk_pause_seconds"])
        question = QUESTION.replace("{captain}", o.captain_name)
        for w in busy:
            delegate = o.delegate_of(w["wing"])
            o.bus.publish("move", JUNO, None, to=f"desk:{delegate.id}")   # Juno walks over
            if pause:
                await asyncio.sleep(pause)
            answer = await o.comms.ask(delegate.id, JUNO, question, visit=False)
            wings.append({**{k: w[k] for k in ("wing", "name", "state", "blockers",
                                               "waiting_on_captain", "people")},
                          "delegate": delegate.nickname, "answer": answer})
        if busy:
            o.bus.publish("move", JUNO, None, to=f"desk:{JUNO}")   # and back to her own desk
        summary = self.summary(wings, digest)
        result = {"mode": mode, "reason": reason, "held": o.held, "clocked_out": o.clocked_out,
                  "wings": wings, "summary": summary, "signature": signature(digest)}
        if busy or reason == "asked":
            o.bus.publish("rounds", JUNO, None, **result)
        if post and busy:
            await o.report_to_captain(JUNO, summary)
        return result

    def summary(self, wings: list[dict], digest: dict) -> str:
        o = self.office
        if not wings:
            return "Rounds: every wing is idle." + (" The office is paused." if o.held else "")
        head = ("Rounds while the office is paused (work waits where it is):" if o.held
                else "Rounds:")
        lines = [f"{w['name']} ({w['delegate']}): {w['answer']}" for w in wings]
        if o.clocked_out:
            lines.append("The office has clocked out for the day (budget).")
        return head + "\n" + "\n".join(lines)

    # the timer ------------------------------------------------------------------------------
    async def tick(self) -> dict | None:
        """One timed check. While the office runs, Juno walks every interval and posts to the
        Captain when something changed (and every few rounds as a heartbeat). While paused, she
        walks only when something changed, using the free code roll-up."""
        o = self.office
        juno = o.agents[JUNO]
        if self._lock.locked() or juno.current_task is not None:
            return None   # she is busy: no overlapping walks
        digest = office_digest(o)
        if not digest["active"]:
            self._last_signature, self._ticks = None, 0
            return None
        sig = signature(digest)
        changed = sig != self._last_signature
        self._ticks += 1
        if o.held and not changed:
            return None
        heartbeat = self._ticks % max(1, int(settings()["heartbeat_ticks"])) == 0
        self._last_signature = sig
        return await self.walk(reason="timer", post=changed or heartbeat)

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(max(5, float(settings()["interval_seconds"])))
            if not settings()["enabled"]:
                continue
            try:
                await self.tick()
            except Exception:   # a failed round never hurts the office
                log.exception("rounds failed")
