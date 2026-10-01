"""SQLite persistence: tasks, conversations, chat messages, events, spend and incidents.

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

    def resolve_incident(self, incident_id: int) -> None:
        self._exec("UPDATE incidents SET resolved=1 WHERE id=?", (incident_id,))

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
