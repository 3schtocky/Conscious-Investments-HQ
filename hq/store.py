"""SQLite persistence: tasks, conversations, chat messages, events, spend, incidents and audit.

One connection per Store, guarded by a lock so the asyncio office and its worker threads can
share it. Everything stays local in data/ (gitignored).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    assignee TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    kind TEXT NOT NULL,              -- assignment | message | delegation
    parent_id INTEGER,
    root_id INTEGER,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued | running | done | paused | paused_budget | declined | error
    status_reason TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    task_id INTEGER PRIMARY KEY,
    system TEXT NOT NULL,
    messages TEXT NOT NULL           -- JSON list, append-only (preserved thinking)
);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    channel TEXT NOT NULL,           -- dm:<a>|<b> | wing:<wing> | lobby:<meeting> | captain
    sender TEXT NOT NULL,
    recipients TEXT NOT NULL,        -- JSON list
    text TEXT NOT NULL,
    task_id INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    agent TEXT,
    task_id INTEGER,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    day TEXT NOT NULL,               -- office-local date, for the daily cap
    agent TEXT NOT NULL,
    task_id INTEGER,
    root_id INTEGER,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost REAL NOT NULL,
    request_id TEXT
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    agent TEXT,
    task_id INTEGER,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,              -- brief | model | conflict | portfolio | newsletter | other
    agent TEXT NOT NULL,             -- who asked
    task_id INTEGER,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload TEXT NOT NULL,           -- JSON: ticker, version, attachments, ...
    status TEXT NOT NULL,            -- pending | approved | changes | rejected
    decided_ts REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,            -- draft | awaiting | approved | changes | rejected | superseded
    path TEXT NOT NULL,              -- the .xlsx
    created REAL NOT NULL,
    created_by TEXT NOT NULL,
    summary TEXT NOT NULL,           -- JSON: price targets, rating, returns, check result
    approval_id INTEGER,
    approved_at REAL,
    UNIQUE (ticker, version)
);
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL,
    added REAL NOT NULL,
    added_by TEXT NOT NULL,
    source TEXT NOT NULL,            -- e.g. "gems 2026-09-30 #3"
    thesis TEXT NOT NULL,
    pitch TEXT,                      -- file path inside coverage/<TICKER>/
    price_at_add REAL,
    spy_at_add REAL,
    status TEXT NOT NULL,            -- watching | researching | dropped
    UNIQUE (ticker, source)
);
CREATE TABLE IF NOT EXISTS audit_findings (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    agent TEXT,                      -- whose work was checked
    task_id INTEGER,
    root_id INTEGER,
    rule TEXT NOT NULL,              -- e.g. unapproved_figures, verify_left, tool_errors
    severity TEXT NOT NULL,          -- flag (Vera reviews) | note (digest only)
    subject TEXT NOT NULL,           -- what was checked, e.g. "RMBS memo.md"
    detail TEXT NOT NULL,
    status TEXT NOT NULL,            -- open | reviewing | cleared | upheld | dismissed
    review_task_id INTEGER,
    resolved_by TEXT,
    resolved_ts REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS memory_writes (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    task_id INTEGER,
    kind TEXT NOT NULL,              -- desk | wiki
    text TEXT NOT NULL,
    reasons TEXT NOT NULL,           -- JSON list: why the code screen held it (empty if clean)
    status TEXT NOT NULL,            -- saved | pending | approved | rejected | removed
    decided_ts REAL
);
CREATE TABLE IF NOT EXISTS digests (
    day TEXT PRIMARY KEY,            -- office-local date
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    data TEXT NOT NULL               -- JSON: the numbers behind the text
);
CREATE TABLE IF NOT EXISTS pauses (
    agent TEXT PRIMARY KEY,          -- survives a restart: only the Captain unpauses
    by TEXT NOT NULL,
    reason TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS spend_day ON spend(day);
CREATE INDEX IF NOT EXISTS spend_root ON spend(root_id);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id);
"""


class Store:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            # Migrations for databases created before a column existed.
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(chat)")}
            if "original" not in cols:   # the Captain's words before the tone rewrite
                self._db.execute("ALTER TABLE chat ADD COLUMN original TEXT")

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # tasks -------------------------------------------------------------------------------
    def create_task(self, *, assignee: str, assigned_by: str, kind: str, title: str, body: str,
                    parent_id: int | None = None, root_id: int | None = None) -> int:
        now = time.time()
        cur = self._exec(
            "INSERT INTO tasks (created, updated, assignee, assigned_by, kind, parent_id, root_id,"
            " title, body, status) VALUES (?,?,?,?,?,?,?,?,?, 'queued')",
            (now, now, assignee, assigned_by, kind, parent_id, root_id, title, body),
        )
        task_id = cur.lastrowid
        if root_id is None:
            self._exec("UPDATE tasks SET root_id=? WHERE id=?", (task_id, task_id))
        return task_id

    def task(self, task_id: int) -> dict:
        rows = self._all("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise KeyError(f"no task {task_id}")
        return rows[0]

    def children(self, task_id: int, kind: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM tasks WHERE parent_id=?", (task_id,)
        if kind:
            sql, args = sql + " AND kind=?", (task_id, kind)
        return self._all(sql + " ORDER BY id", args)

    def tasks_between(self, start: float, end: float, *, by: str = "created") -> list[dict]:
        col = "updated" if by == "updated" else "created"
        return self._all(f"SELECT * FROM tasks WHERE {col}>=? AND {col}<? ORDER BY id", (start, end))

    def tasks(self, status: str | None = None) -> list[dict]:
        if status:
            return self._all("SELECT * FROM tasks WHERE status=? ORDER BY id", (status,))
        return self._all("SELECT * FROM tasks ORDER BY id")

    def set_task_status(self, task_id: int, status: str, reason: str | None = None,
                        result: str | None = None) -> None:
        self._exec(
            "UPDATE tasks SET status=?, status_reason=?, result=COALESCE(?, result), updated=?"
            " WHERE id=?",
            (status, reason, result, time.time(), task_id),
        )

    # conversations -----------------------------------------------------------------------
    def save_conversation(self, task_id: int, system: str, messages: list[dict]) -> None:
        self._exec(
            "INSERT INTO conversations (task_id, system, messages) VALUES (?,?,?)"
            " ON CONFLICT(task_id) DO UPDATE SET messages=excluded.messages",
            (task_id, system, json.dumps(messages)),
        )

    def conversation(self, task_id: int) -> tuple[str, list[dict]] | None:
        rows = self._all("SELECT system, messages FROM conversations WHERE task_id=?", (task_id,))
        if not rows:
            return None
        return rows[0]["system"], json.loads(rows[0]["messages"])

    # chat --------------------------------------------------------------------------------
    def add_chat(self, *, channel: str, sender: str, recipients: list[str], text: str,
                 task_id: int | None = None, original: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO chat (ts, channel, sender, recipients, text, task_id, original)"
            " VALUES (?,?,?,?,?,?,?)",
            (time.time(), channel, sender, json.dumps(recipients), text, task_id, original),
        )
        return cur.lastrowid

    def chat(self, channel: str | None = None, limit: int = 200) -> list[dict]:
        if channel:
            rows = self._all("SELECT * FROM chat WHERE channel=? ORDER BY id DESC LIMIT ?",
                             (channel, limit))
        else:
            rows = self._all("SELECT * FROM chat ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["recipients"] = json.loads(r["recipients"])
        return rows[::-1]

    # events ------------------------------------------------------------------------------
    def add_event(self, type_: str, agent: str | None, task_id: int | None,
                  payload: dict[str, Any]) -> int:
        cur = self._exec(
            "INSERT INTO events (ts, type, agent, task_id, payload) VALUES (?,?,?,?,?)",
            (time.time(), type_, agent, task_id, json.dumps(payload, default=str)),
        )
        return cur.lastrowid

    def events_of_type(self, type_: str) -> list[dict]:
        rows = self._all("SELECT * FROM events WHERE type=? ORDER BY id", (type_,))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def events_where(self, *, agent: str | None = None, task_ids: list[int] | None = None,
                     types: list[str] | None = None, start: float | None = None,
                     end: float | None = None, limit: int = 500) -> list[dict]:
        """The newest `limit` events matching every filter given, oldest first."""
        where, args = [], []
        if agent:
            where.append("agent=?")
            args.append(agent)
        if task_ids is not None:
            where.append(f"task_id IN ({','.join('?' * len(task_ids)) or 'NULL'})")
            args += task_ids
        if types:
            where.append(f"type IN ({','.join('?' * len(types))})")
            args += types
        if start is not None:
            where.append("ts>=?")
            args.append(start)
        if end is not None:
            where.append("ts<?")
            args.append(end)
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._all(f"SELECT * FROM ({sql} ORDER BY id DESC LIMIT ?) ORDER BY id",
                         (*args, limit))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def count_tool_errors(self, start: float, end: float) -> int:
        return self._all("SELECT COUNT(*) AS n FROM events WHERE type='tool_result' AND ts>=? AND ts<?"
                         " AND json_extract(payload, '$.is_error')", (start, end))[0]["n"]

    def events(self, task_id: int | None = None, limit: int = 500) -> list[dict]:
        if task_id is not None:
            rows = self._all("SELECT * FROM events WHERE task_id=? ORDER BY id LIMIT ?",
                             (task_id, limit))
        else:
            rows = self._all("SELECT * FROM (SELECT * FROM events ORDER BY id DESC LIMIT ?)"
                             " ORDER BY id", (limit,))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    # spend -------------------------------------------------------------------------------
    def add_spend(self, *, day: str, agent: str, task_id: int | None, root_id: int | None,
                  model: str, usage: dict[str, int], cost: float,
                  request_id: str | None) -> None:
        self._exec(
            "INSERT INTO spend (ts, day, agent, task_id, root_id, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost, request_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), day, agent, task_id, root_id, model, usage.get("input_tokens", 0),
             usage.get("output_tokens", 0), usage.get("cache_read_input_tokens", 0),
             usage.get("cache_creation_input_tokens", 0), cost, request_id),
        )

    def spend_for_day(self, day: str) -> float:
        rows = self._all("SELECT COALESCE(SUM(cost), 0) AS c FROM spend WHERE day=?", (day,))
        return float(rows[0]["c"])

    def spend_for_root(self, root_id: int) -> float:
        rows = self._all("SELECT COALESCE(SUM(cost), 0) AS c FROM spend WHERE root_id=?",
                         (root_id,))
        return float(rows[0]["c"])

    def spend_for_task(self, task_id: int) -> float:
        rows = self._all("SELECT COALESCE(SUM(cost), 0) AS c FROM spend WHERE task_id=?",
                         (task_id,))
        return float(rows[0]["c"])

    def spend_breakdown(self, day: str) -> list[dict]:
        return self._all(
            "SELECT agent, model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
            " SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens,"
            " SUM(cost) AS cost FROM spend WHERE day=? GROUP BY agent, model ORDER BY cost DESC",
            (day,),
        )

    def clear_spend(self) -> None:
        """Demo only: wipe recorded spend (the demo database is separate and disposable)."""
        self._exec("DELETE FROM spend")

    # incidents ---------------------------------------------------------------------------
    def add_incident(self, *, agent: str | None, task_id: int | None, kind: str,
                     detail: str) -> int:
        cur = self._exec(
            "INSERT INTO incidents (ts, agent, task_id, kind, detail) VALUES (?,?,?,?,?)",
            (time.time(), agent, task_id, kind, detail),
        )
        return cur.lastrowid

    def incidents(self, unresolved_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM incidents" + (" WHERE resolved=0" if unresolved_only else "")
        return self._all(sql + " ORDER BY id")

    def incidents_between(self, start: float, end: float) -> list[dict]:
        return self._all("SELECT * FROM incidents WHERE ts>=? AND ts<? ORDER BY id", (start, end))

    def resolve_incident(self, incident_id: int) -> None:
        self._exec("UPDATE incidents SET resolved=1 WHERE id=?", (incident_id,))

    # audit findings ----------------------------------------------------------------------
    def add_finding(self, *, agent: str | None, task_id: int | None, root_id: int | None,
                    rule: str, severity: str, subject: str, detail: str) -> tuple[int, bool]:
        """Record a code-check finding; returns (id, is_new). The same finding is recorded once
        per task (a file is checked when it's filed for approval and again when the task ends)
        and once per piece of work while it is still waiting for a ruling. After a ruling, the
        same problem turning up again in later work is a new finding."""
        sql = ("SELECT id FROM audit_findings WHERE rule=? AND subject=? AND detail=? AND ("
               "(root_id IS ? AND status IN ('open','reviewing'))")
        args: tuple = (rule, subject, detail, root_id)
        if task_id is not None:
            sql, args = sql + " OR task_id=?", (*args, task_id)
        dup = self._all(sql + ") ORDER BY id DESC LIMIT 1", args)
        if dup:
            return dup[0]["id"], False
        cur = self._exec(
            "INSERT INTO audit_findings (ts, agent, task_id, root_id, rule, severity, subject,"
            " detail, status) VALUES (?,?,?,?,?,?,?,?, 'open')",
            (time.time(), agent, task_id, root_id, rule, severity, subject, detail))
        return cur.lastrowid, True

    def count_findings(self, status: str, severity: str) -> int:
        return self._all("SELECT COUNT(*) AS n FROM audit_findings WHERE status=? AND severity=?",
                         (status, severity))[0]["n"]

    def reopen_findings(self, review_task_id: int) -> int:
        """Findings a review task left without a ruling go back to open."""
        cur = self._exec("UPDATE audit_findings SET status='open' WHERE status='reviewing'"
                         " AND review_task_id=?", (review_task_id,))
        return cur.rowcount

    def finding(self, finding_id: int) -> dict:
        rows = self._all("SELECT * FROM audit_findings WHERE id=?", (finding_id,))
        if not rows:
            raise KeyError(f"no audit finding {finding_id}")
        return rows[0]

    def findings(self, *, statuses: tuple[str, ...] | None = None, start: float | None = None,
                 end: float | None = None, limit: int = 200) -> list[dict]:
        where, args = [], []
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args += statuses
        if start is not None:
            where.append("ts>=?")
            args.append(start)
        if end is not None:
            where.append("ts<?")
            args.append(end)
        sql = "SELECT * FROM audit_findings" + (" WHERE " + " AND ".join(where) if where else "")
        return self._all(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))[::-1]

    def set_finding(self, finding_id: int, status: str, *, by: str | None = None,
                    note: str | None = None, review_task_id: int | None = None) -> None:
        done = status in ("cleared", "upheld", "dismissed")
        self._exec(
            "UPDATE audit_findings SET status=?, resolved_by=COALESCE(?, resolved_by),"
            " note=COALESCE(?, note), review_task_id=COALESCE(?, review_task_id),"
            " resolved_ts=CASE WHEN ? THEN ? ELSE resolved_ts END WHERE id=?",
            (status, by, note, review_task_id, done, time.time(), finding_id))

    # memory writes -----------------------------------------------------------------------
    def add_memory_write(self, *, agent: str, task_id: int | None, kind: str, text: str,
                         reasons: list[str], status: str) -> int:
        cur = self._exec(
            "INSERT INTO memory_writes (ts, agent, task_id, kind, text, reasons, status)"
            " VALUES (?,?,?,?,?,?,?)",
            (time.time(), agent, task_id, kind, text, json.dumps(reasons), status))
        return cur.lastrowid

    def memory_write(self, write_id: int) -> dict:
        rows = self.memory_writes(write_id=write_id)
        if not rows:
            raise KeyError(f"no memory write {write_id}")
        return rows[0]

    def memory_writes(self, *, status: str | None = None, write_id: int | None = None,
                      start: float | None = None, end: float | None = None,
                      limit: int = 200) -> list[dict]:
        where, args = [], []
        if write_id is not None:
            where.append("id=?")
            args.append(write_id)
        if status:
            where.append("status=?")
            args.append(status)
        if start is not None:
            where.append("ts>=?")
            args.append(start)
        if end is not None:
            where.append("ts<?")
            args.append(end)
        sql = "SELECT * FROM memory_writes" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self._all(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))[::-1]
        for r in rows:
            r["reasons"] = json.loads(r["reasons"])
        return rows

    def count_memory(self, status: str) -> int:
        return self._all("SELECT COUNT(*) AS n FROM memory_writes WHERE status=?", (status,))[0]["n"]

    def set_memory_status(self, write_id: int, status: str) -> None:
        self._exec("UPDATE memory_writes SET status=?, decided_ts=? WHERE id=?",
                   (status, time.time(), write_id))

    # digests -----------------------------------------------------------------------------
    def save_digest(self, day: str, text: str, data: dict) -> None:
        self._exec("INSERT INTO digests (day, ts, text, data) VALUES (?,?,?,?)"
                   " ON CONFLICT(day) DO UPDATE SET ts=excluded.ts, text=excluded.text,"
                   " data=excluded.data", (day, time.time(), text, json.dumps(data, default=str)))

    def digest(self, day: str) -> dict | None:
        rows = self._all("SELECT * FROM digests WHERE day=?", (day,))
        if not rows:
            return None
        rows[0]["data"] = json.loads(rows[0]["data"])
        return rows[0]

    def digests(self, limit: int = 30) -> list[dict]:
        rows = self._all("SELECT * FROM digests ORDER BY day DESC LIMIT ?", (limit,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    # pauses ------------------------------------------------------------------------------
    def set_pause(self, agent: str, by: str, reason: str) -> None:
        self._exec("INSERT INTO pauses (agent, by, reason, ts) VALUES (?,?,?,?)"
                   " ON CONFLICT(agent) DO UPDATE SET by=excluded.by, reason=excluded.reason,"
                   " ts=excluded.ts", (agent, by, reason, time.time()))

    def clear_pause(self, agent: str) -> None:
        self._exec("DELETE FROM pauses WHERE agent=?", (agent,))

    def pauses(self) -> list[dict]:
        return self._all("SELECT * FROM pauses ORDER BY ts")

    # watchlist ---------------------------------------------------------------------------
    def add_watch(self, *, ticker: str, added_by: str, source: str, thesis: str,
                  pitch: str | None, price: float | None, spy: float | None) -> int:
        """Add (or refresh) a watchlist name; the same ticker from the same screen updates."""
        existing = self._all("SELECT id FROM watchlist WHERE ticker=? AND source=?", (ticker, source))
        if existing:
            self._exec("UPDATE watchlist SET thesis=?, pitch=COALESCE(?, pitch),"
                       " status=CASE WHEN status='dropped' THEN 'watching' ELSE status END WHERE id=?",
                       (thesis, pitch, existing[0]["id"]))
            return existing[0]["id"]
        cur = self._exec(
            "INSERT INTO watchlist (ticker, added, added_by, source, thesis, pitch, price_at_add,"
            " spy_at_add, status) VALUES (?,?,?,?,?,?,?,?, 'watching')",
            (ticker, time.time(), added_by, source, thesis, pitch, price, spy))
        return cur.lastrowid

    def watchlist(self, include_dropped: bool = False) -> list[dict]:
        sql = "SELECT * FROM watchlist" + ("" if include_dropped else " WHERE status!='dropped'")
        return self._all(sql + " ORDER BY added DESC")

    def set_watch_status(self, watch_id: int, status: str) -> None:
        self._exec("UPDATE watchlist SET status=? WHERE id=?", (status, watch_id))

    def watch(self, watch_id: int) -> dict:
        rows = self._all("SELECT * FROM watchlist WHERE id=?", (watch_id,))
        if not rows:
            raise KeyError(f"no watchlist entry {watch_id}")
        return rows[0]

    # model registry ----------------------------------------------------------------------
    def next_model_version(self, ticker: str) -> int:
        rows = self._all("SELECT COALESCE(MAX(version), 0) AS v FROM models WHERE ticker=?",
                         (ticker,))
        return int(rows[0]["v"]) + 1

    def add_model(self, *, ticker: str, version: int, path: str, created_by: str,
                  summary: dict) -> int:
        cur = self._exec(
            "INSERT INTO models (ticker, version, status, path, created, created_by, summary)"
            " VALUES (?,?, 'draft', ?,?,?,?)",
            (ticker, version, path, time.time(), created_by, json.dumps(summary, default=float)))
        return cur.lastrowid

    def models(self, ticker: str | None = None) -> list[dict]:
        if ticker:
            rows = self._all("SELECT * FROM models WHERE ticker=? ORDER BY version", (ticker,))
        else:
            rows = self._all("SELECT * FROM models ORDER BY ticker, version")
        for r in rows:
            r["summary"] = json.loads(r["summary"])
        return rows

    def model(self, ticker: str, version: int) -> dict | None:
        rows = [m for m in self.models(ticker) if m["version"] == version]
        return rows[0] if rows else None

    def set_model_status(self, ticker: str, version: int, status: str, *,
                         approval_id: int | None = None) -> None:
        now = time.time()
        if status == "approved":   # one official version per ticker
            self._exec("UPDATE models SET status='superseded' WHERE ticker=? AND status='approved'",
                       (ticker,))
        self._exec(
            "UPDATE models SET status=?, approval_id=COALESCE(?, approval_id),"
            " approved_at=CASE WHEN ?='approved' THEN ? ELSE approved_at END"
            " WHERE ticker=? AND version=?",
            (status, approval_id, status, now, ticker, version))

    def approved_model(self, ticker: str) -> dict | None:
        rows = [m for m in self.models(ticker) if m["status"] == "approved"]
        return rows[-1] if rows else None

    # approvals ---------------------------------------------------------------------------
    def add_approval(self, *, kind: str, agent: str, task_id: int | None, title: str,
                     summary: str, payload: dict) -> int:
        cur = self._exec(
            "INSERT INTO approvals (ts, kind, agent, task_id, title, summary, payload, status)"
            " VALUES (?,?,?,?,?,?,?, 'pending')",
            (time.time(), kind, agent, task_id, title, summary, json.dumps(payload)),
        )
        return cur.lastrowid

    def approval(self, approval_id: int) -> dict:
        rows = self.approvals(approval_id=approval_id)
        if not rows:
            raise KeyError(f"no approval {approval_id}")
        return rows[0]

    def set_approval_payload(self, approval_id: int, payload: dict) -> None:
        self._exec("UPDATE approvals SET payload=? WHERE id=?", (json.dumps(payload), approval_id))

    def approvals(self, status: str | None = None, approval_id: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM approvals", ()
        if approval_id is not None:
            sql, args = sql + " WHERE id=?", (approval_id,)
        elif status:
            sql, args = sql + " WHERE status=?", (status,)
        rows = self._all(sql + " ORDER BY id", args)
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def decide_approval(self, approval_id: int, status: str, note: str | None) -> None:
        self._exec("UPDATE approvals SET status=?, note=?, decided_ts=? WHERE id=?",
                   (status, note, time.time(), approval_id))
