"""Turn a recorded demo run into the timed script every visitor's browser plays.

The recording (`hq.demo_micron.record`) comes from the real engine, so the timings are whatever the
engine did. The script re-times it onto a fixed length (300 seconds) so each line can be read, keeps
only the event types and fields a visitor needs, and marks when each deliverable unlocks. Words are
allowed here and nowhere else on the public site: every line was written in `hq.demo_micron`, none of it
comes from the Captain or from a real agent.
"""

from __future__ import annotations

import re
from typing import Any

DURATION = 340.0
END_CARD = 5.0          # seconds at the end for the closing frame
# What a visitor's floor and feed need, field by field. Anything else is dropped.
FIELDS: dict[str, tuple[str, ...]] = {
    "status": ("status",),
    "move": ("to",),
    "meeting": ("participants",),
    "chat": ("channel", "recipients", "text"),
    "delegated": ("to", "job"),
    "task_started": ("kind", "title", "assigned_by"),
    "task_done": (),
    "captain_message": ("to", "text"),
    "captain_report": ("text",),
    "approval_requested": ("kind", "title"),
    "approval_decided": ("kind", "title", "decision"),
    "outbox_ready": ("title",),
    "model_built": ("ticker", "version", "ok"),
}
# Seconds a beat holds the stage before the next one, before scaling to the fixed length.
HOLD = {"status": 0.15, "move": 0.45, "task_done": 0.6, "task_started": 1.0, "meeting": 2.2,
        "delegated": 3.0, "approval_requested": 2.5, "approval_decided": 2.5, "outbox_ready": 2.0,
        "model_built": 1.5}
WORDS_PER_SECOND = 3.0   # reading speed for a spoken line
SPEECH = ("chat", "captain_message", "captain_report")


def _placeholders(text: str, names: dict[str, str]) -> str:
    """Current nicknames -> {role_id}, so the player can write whatever name the visitor sees."""
    for nick in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<![\w{{]){re.escape(nick)}(?![\w}}])", "{" + names[nick] + "}", text)
    return text


def _hold(ev: dict) -> float:
    if ev["type"] in SPEECH:
        words = len(str(ev.get("text", "")).split())
        return min(max(1.6 + words / WORDS_PER_SECOND, 2.4), 8.0)
    return HOLD.get(ev["type"], 0.3)


def _pick(ev: dict, names: dict[str, str]) -> dict | None:
    kind = ev["type"]
    if kind not in FIELDS:
        return None
    if kind == "status" and ev.get("status") not in ("idle", "working", "held"):
        return None
    if kind == "chat" and str(ev.get("channel", "")).startswith("group:"):
        return None   # the demo has no group chats; keep the schema narrow
    if kind == "captain_message" and ev.get("to") != "office":
        return None   # "Stott approved your request..." is the system talking, not Stott
    out: dict[str, Any] = {"type": kind, "agent": ev.get("agent")}
    for field in FIELDS[kind]:
        value = ev.get(field)
        if isinstance(value, str):
            value = _placeholders(value, names)
        elif isinstance(value, list):
            value = [_placeholders(v, names) if isinstance(v, str) else v for v in value]
        out[field] = value
    if kind == "chat":
        out["channel"] = "floor"
    return out


def build(events: list[dict], agent_names: dict[str, str], duration: float = DURATION) -> dict:
    """The tour: `beats` (time in seconds from the start, one event each), the `acts` that caption
    the stages and the `unlocks` that reveal each deliverable as it is made."""
    names = {nick: aid for aid, nick in agent_names.items()}
    beats: list[dict] = []
    marks: dict[str, int] = {}
    for ev in events:
        beat = _pick(ev, names)
        if beat is None:
            continue
        i = len(beats)
        if ev["type"] == "captain_message" and "opening" not in marks:
            marks["opening"] = i
        if ev["type"] == "approval_requested" and ev.get("kind") == "model":
            marks.setdefault("model_card", i)
        if ev["type"] == "approval_decided" and ev.get("kind") == "model":
            marks.setdefault("model_approved", i)
        if ev["type"] == "captain_report" and ev.get("agent") == "er_lead":
            marks.setdefault("report", i)
        if ev["type"] == "model_built":
            marks.setdefault("model_built", i)
        if ev["type"] == "outbox_ready":
            marks.setdefault("note", i)
        if ev["type"] == "approval_decided" and ev.get("kind") == "newsletter":
            marks.setdefault("note_approved", i)
        if ev["type"] == "approval_requested" and ev.get("kind") == "portfolio":
            marks.setdefault("position_card", i)
        if ev["type"] == "approval_decided" and ev.get("kind") == "portfolio":
            marks.setdefault("position_open", i)
        if ev["type"] == "meeting":
            marks.setdefault("wrap", i)
        beats.append(beat)
    missing = {"opening", "model_card", "model_approved", "report", "model_built", "note", "note_approved",
               "position_card", "position_open", "wrap"} - set(marks)
    if missing:
        raise ValueError(f"the recording is missing beats: {sorted(missing)}")
    # Re-time into fixed windows, one per act, so the story always has the same shape and length.
    first_report = next(i for i, b in enumerate(beats) if b["type"] == "captain_report")
    asks = [i for i, b in enumerate(beats) if b["type"] == "captain_message"]   # Stott's own words, in order
    if len(asks) < 5:
        raise ValueError(f"expected five messages from Stott, found {len(asks)}")
    approve_msg, buy_msg, wrap_msg = asks[1], asks[2], asks[4]
    bounds = [0, first_report + 1, marks["model_card"], approve_msg, marks["note"], buy_msg, wrap_msg, len(beats)]
    if bounds != sorted(bounds):
        raise ValueError(f"the recording's acts are out of order: {bounds}")
    windows = [28.0, 80.0, 24.0, 90.0, 26.0, 50.0, 37.0]       # seconds per act; they add up to 335
    scale_check = duration - END_CARD
    if abs(sum(windows) - scale_check) > 0.01:
        windows[-1] += scale_check - sum(windows)
    assert len(windows) == len(bounds) - 1
    at: list[float] = []
    start = 0.0
    for lo, hi, win in zip(bounds, bounds[1:], windows):
        hold = [_hold(b) for b in beats[lo:hi]]
        total = sum(hold) or 1.0
        t = start
        for h in hold:
            at.append(round(t, 2))
            t += h * win / total
        start += win
    for b, ts in zip(beats, at, strict=True):
        b["t"] = ts
    stamp = lambda key: at[marks[key]]
    acts = [
        {"t": 0.0, "label": "Stott asks Juno for Micron",
         "caption": "Stott gives the Chief of Staff one instruction. Juno splits it between the desks."},
        {"t": at[bounds[1]], "label": "Research and Quant work in parallel",
         "caption": "Equity Research reads the filings and writes the report. Quant builds the model "
                    "and runs two thousand simulations."},
        {"t": at[bounds[2]], "label": "Stott approves the model",
         "caption": "Nothing becomes a firm number until the Captain signs it. The model is checked "
                    "against the valuation engine first."},
        {"t": at[bounds[3]], "label": "Audit and Client Relations",
         "caption": "Audit ties every figure in the report to the approved model. Client Relations "
                    "drafts a short client note from the approved numbers only."},
        {"t": at[bounds[4]], "label": "The note passes the publishing gate",
         "caption": "Code checks the note for unapproved numbers, hype and advice before the "
                    "Captain sees it."},
        {"t": at[bounds[5]], "label": "Do we own it?",
         "caption": "Stott asks the team to size a position. Research proposes, Audit checks the rules, "
                    "Quant states the downside, and Stott decides."},
        {"t": at[bounds[6]], "label": "Juno wraps up",
         "caption": "One line from each lead, then one report for Stott."},
    ]
    unlocks = [
        {"t": stamp("report"), "artifact": "report", "label": "Initiating-coverage report"},
        {"t": stamp("model_built"), "artifact": "model", "label": "Bull, base and bear model"},
        {"t": stamp("note"), "artifact": "note", "label": "Client note"},
        {"t": stamp("position_open"), "artifact": "position", "label": "Open position"},
    ]
    return {"ticker": "MU", "duration": duration, "end_card": duration - END_CARD,
            "beats": beats, "acts": acts, "unlocks": unlocks}
