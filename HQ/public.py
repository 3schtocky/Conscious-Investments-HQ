"""Public mode: the office on the open internet, watched by anyone, run by one person.

`hq serve --public` is for putting the office behind a Cloudflare Tunnel. In that mode there are
two kinds of caller and nothing in between:

- **The Captain**, who signed in with the password from `.env` and holds a session cookie. He
  gets the whole office, exactly as on his own machine.
- **Visitors**, everyone else. They can watch the floor and read finished work. They can never
  change anything, start any work or spend anything, and the server only ever hands them the
  sanitized views built in this file. Everything else is refused by default: a new endpoint is
  private until it is added to the visitor allowlist on purpose.

What visitors get (Stott, 2026-10-02): the office floor live (who is at their desk, who is
working, who walks where), the watchlist, the paper portfolio scoreboard and approved
newsletters. Hidden: agents' reasoning and what they say to each other, drafts, models and
research files, approval cards, audit findings, spend, memory, and everything the Captain writes.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from typing import TYPE_CHECKING, Any

from HQ.config import office as office_config

if TYPE_CHECKING:
    from HQ.engine.runtime import Office

PASSWORD_ENV = "HQ_CAPTAIN_PASSWORD"
MIN_PASSWORD = 16
COOKIE = "hq_session"
SESSION_SECONDS = 14 * 24 * 3600
MAX_VISITOR_SOCKETS = 300          # all visitors together: protects the machine the office runs on
MAX_SOCKETS_PER_ADDRESS = 8        # one visitor (a few tabs), so nobody can take every slot
SOCKET_PING_SECONDS = 25           # a quiet stream still notices a visitor who left
NEWSLETTER_FILES = ("issue.html", "body.html", "header.png")


def hosts() -> set[str]:
    """The public host names the office answers to, from office.yaml (`public.hosts`)."""
    cfg = office_config().get("public", {}) or {}
    return {str(h).strip().lower() for h in cfg.get("hosts", []) if str(h).strip()}


def password() -> str:
    return os.environ.get(PASSWORD_ENV, "")


def check_ready() -> None:
    """Refuse to go public without a strong Captain password and a host name to answer to."""
    if len(password()) < MIN_PASSWORD:
        raise SystemExit(
            f"Public mode needs a Captain password of at least {MIN_PASSWORD} characters in .env "
            f"({PASSWORD_ENV}). Create one with: uv run hq captain-password")
    if not hosts():
        raise SystemExit("Public mode needs the site's host name under public.hosts in "
                         "HQ/settings/office.yaml (e.g. consciousinvestments.org).")


class Sessions:
    """Captain sessions, held in memory: a restart signs him out, which is the safe default."""

    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}

    def create(self) -> str:
        now = time.time()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        token = secrets.token_urlsafe(32)
        self._tokens[token] = now + SESSION_SECONDS
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        exp = self._tokens.get(token)
        if exp is None or exp <= time.time():
            self._tokens.pop(token, None)
            return False
        return True

    def drop(self, token: str | None) -> None:
        self._tokens.pop(token or "", None)


class LoginLimiter:
    """Slow password guessing to a crawl without letting a stranger lock the Captain out: five
    wrong tries from one address lock that address for fifteen minutes, and nobody else."""

    WINDOW, PER_ADDRESS = 15 * 60, 5

    def __init__(self) -> None:
        self._fails: list[tuple[float, str]] = []

    def _recent(self) -> list[tuple[float, str]]:
        cutoff = time.time() - self.WINDOW
        self._fails = [f for f in self._fails if f[0] > cutoff][-5000:]
        return self._fails

    def locked(self, address: str) -> bool:
        return sum(1 for _, a in self._recent() if a == address) >= self.PER_ADDRESS

    def failed(self, address: str) -> None:
        self._fails.append((time.time(), address))

    def succeeded(self, address: str) -> None:
        self._fails = [f for f in self._fails if f[1] != address]


def password_matches(given: str) -> bool:
    expected = password()
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


# ---- what a visitor may see ------------------------------------------------------------------
def task_label(office: Office, *, title: str, kind: str, assigned_by: str) -> str:
    """What a visitor is told an agent is doing. Only a title the Chief of Staff wrote is shown
    as written; anything that could quote the Captain, a colleague or an audit is described."""
    if kind == "delegation":
        lead = office.name(assigned_by) if assigned_by in office.agents else "the lead"
        return f"A job for {lead}"
    if kind == "message":
        return "Answering a colleague"
    if assigned_by == "chief_of_staff":
        return title
    if assigned_by in ("audit_associate", "audit_lead", "office"):
        return "An audit review"
    return f"An assignment from {office.captain_name}"


def state(office: Office, *, demo: bool) -> dict:
    """The snapshot for a visitor's page: the cast and what each is doing, nothing they said.
    Built field by field from the cast, so nothing private is ever loaded and then stripped."""
    from HQ.config import roster

    cast = roster()
    avatars = {e["id"]: e.get("avatar") for e in cast["agents"]}
    agents = []
    for a in office.agents.values():
        task = None
        if a.current_task is not None:
            t = office.store.task(a.current_task)
            task = {"id": t["id"], "title": task_label(office, title=t["title"], kind=t["kind"],
                                                       assigned_by=t["assigned_by"])}
        agents.append({"id": a.id, "nickname": a.nickname, "wing": a.wing, "role": a.role,
                       "persona": a.persona, "model": a.model_key, "model_id": a.model_cfg["id"],
                       "tier": a.tier, "avatar": avatars.get(a.id), "status": a.status,
                       "paused": a.paused, "task": task})
    captain = cast.get("captain", {}) or {}
    return {
        "office": {"clocked_out": office.clocked_out, "held": office.held, "day": office.ledger.today(), "demo": demo,
                   "max_concurrent": office_config()["limits"]["max_concurrent_agents"]},
        "wings": {k: v["name"] for k, v in cast["wings"].items()},
        "captain": {"nickname": office.captain_name, "avatar": captain.get("avatar")},
        "models": {k: v["id"] for k, v in office_config()["models"].items()},
        "agents": agents, "captain_name": office.captain_name,
        # The page's state loader expects these; a visitor's are always empty.
        "spend": {"today": 0, "cap": 0, "by_agent": {}, "by_model": {}},
        "events": [], "chat": [], "incidents": [],
    }


def event(office: Office, ev: dict[str, Any]) -> dict | None:
    """One live event, cut down to what moves the picture. Words never pass: a chat becomes an
    ellipsis so the speaker still gets a bubble, and everything not listed here is dropped."""
    kind, agent = ev.get("type"), ev.get("agent")
    if agent is not None and agent not in office.agents:
        return None   # the Captain's own posts, and anything not from a colleague
    base = {"id": ev.get("id"), "ts": ev.get("ts"), "type": kind, "agent": agent, "task_id": None}
    if kind == "status":
        status = ev.get("status")
        return {**base, "status": status} if status in ("idle", "working", "paused", "held") else None
    if kind == "move":
        return {**base, "to": ev.get("to")}
    if kind == "meeting":
        return {**base, "participants": [p for p in ev.get("participants", []) if p in office.agents]}
    if kind == "chat":
        recipients = [r for r in ev.get("recipients", []) if r in office.agents]
        channel = str(ev.get("channel", ""))
        return {**base, "channel": "group" if channel.startswith("group:") else "floor",
                "recipients": recipients, "text": "…"}
    if kind == "delegated":
        to = ev.get("to")
        return {**base, "to": to, "job": "…"} if to in office.agents else None
    if kind == "task_started":
        return {**base, "kind": ev.get("kind"),
                "title": task_label(office, title=str(ev.get("title", "")), kind=str(ev.get("kind", "")),
                                    assigned_by=str(ev.get("assigned_by", "")))}
    if kind == "task_done":
        return base
    if kind == "office_status":
        return {**base, "agent": None, "status": ev.get("status")}
    if kind == "office_hold":
        return {**base, "agent": None, "held": bool(ev.get("held"))}
    if kind in ("watchlist_added", "watchlist_status", "portfolio_changed", "roster_updated"):
        return {**base, "agent": None}   # a nudge to fetch the public view again, no details
    if kind == "outbox_status" and ev.get("status") == "approved":
        return {**base, "agent": None, "status": "approved"}
    return None


REPLAY_TYPES = ["status", "move", "meeting", "chat", "delegated", "task_started", "task_done"]
REPLAY_DAYS = 7          # how far back a replay may reach
REPLAY_MAX = 150         # events in one replay
REPLAY_SESSION_GAP = 6 * 3600   # a longer silence ends the stretch of work being replayed


def replay(office: Office, now: float | None = None, max_events: int = REPLAY_MAX) -> dict:
    """The most recent stretch of real floor activity, for the page to play back when the office
    is quiet. Every event passes through `event()`, the same filter as the live stream, so a
    replay can never show a visitor more than watching live would: movement and who works, never
    words. Oldest first."""
    now = now or time.time()
    rows = office.store.events_where(types=REPLAY_TYPES, start=now - REPLAY_DAYS * 24 * 3600, limit=max(600, max_events * 3))
    safe = []
    for r in rows:
        ev = event(office, {"id": r["id"], "ts": r["ts"], "type": r["type"], "agent": r["agent"],
                            "task_id": r["task_id"], **r["payload"]})
        if ev is not None:
            safe.append(ev)
    session: list[dict] = []
    for ev in reversed(safe):   # newest back to the first long silence
        if session and session[-1]["ts"] - ev["ts"] > REPLAY_SESSION_GAP:
            break
        session.append(ev)
        if len(session) >= max_events:
            break
    session.reverse()
    return {"events": session, "from": session[0]["ts"] if session else None,
            "to": session[-1]["ts"] if session else None}


def health(office: Office) -> dict:
    """Just enough for an uptime monitor or the page itself: the office answers, and whether it
    is taking work. Nothing about what it is doing."""
    return {"ok": True, "paused": office.held, "clocked_out": office.clocked_out}


def watchlist(office: Office, prices: dict) -> list[dict]:
    keep = ("id", "ticker", "added", "source", "thesis", "price_at_add", "status", "price_now",
            "return", "spy_return", "vs_spy", "added_by_name")
    return [{k: w.get(k) for k in keep} | {"added_by": "", "pitch": None}
            for w in office.watchlist_view(prices) if w["status"] != "dropped"]


POSITION_FIELDS = ("id", "ticker", "status", "size_pct", "entry_price", "entry_ts", "entry_day",
                   "exit_ts", "exit_day", "price", "value", "weight", "return", "spy_return", "vs_spy",
                   "base_target", "rating", "to_target", "thesis", "proposed_by_name")
SCORE_FIELDS = ("benchmark", "starting_capital", "value", "cash", "invested", "since", "return",
                "benchmark_return", "vs_benchmark", "picks_beating", "picks_judged",
                "average_pick_vs_benchmark", "sizes", "note", "priced", "summary", "history")


def portfolio(office: Office, prices: dict) -> dict:
    """The scoreboard and positions the Captain approved, by keep-list: a field added to a
    position later stays private until it is named here. The thesis is the case he approved on
    the card; exit reasons and cards still on his desk are not shown."""
    view = office.portfolio_view(prices)
    row = lambda r: {k: r.get(k) for k in POSITION_FIELDS} | {"exit_reason": None}
    return ({k: view.get(k) for k in SCORE_FIELDS}
            | {"open": [row(r) for r in view["open"]], "closed": [row(r) for r in view["closed"]],
               "pending": []})


def newsletters(office: Office) -> list[dict]:
    """Issues the Captain approved, newest first. Drafts and issues awaiting him never appear."""
    from HQ import outbox

    return [{"id": m["id"], "title": m["title"], "date": m["date"], "words": m["words"],
             "updated": m["updated"],
             "files": {k: f"/public/newsletters/{m['id']}/{k}" for k in NEWSLETTER_FILES
                       if k in m.get("files", {})}}
            for m in outbox.issues(office.outbox_dir) if m["status"] == "approved"]


def newsletter_file(office: Office, issue_id: str, name: str):
    """Path of one file of an approved issue, or None."""
    from HQ import outbox

    if name not in NEWSLETTER_FILES:
        return None
    try:
        meta = outbox.load(office.outbox_dir, issue_id)
    except KeyError:
        return None
    if meta["status"] != "approved":
        return None
    path = outbox.issue_dir(office.outbox_dir, issue_id) / name
    return path if path.is_file() else None
