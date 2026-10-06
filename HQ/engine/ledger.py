"""Spend accounting and the budget gate.

`usage_cost` prices one API response. `Ledger` records every response and decides whether a
new model call may start. The rule, from the Captain's choice: no new call starts once today's
spend reaches the cap; a call already in flight finishes and is recorded (so the day can end
slightly over the cap, by at most the in-flight calls). Audit agents may keep going into a
small reserve so they can still raise an alert.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from HQ.config import office
from HQ.store import Store

PER_MTOK = 1_000_000


def _get(obj: Any, name: str) -> int:
    val = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return int(val or 0)


def usage_dict(usage: Any) -> dict[str, Any]:
    """Normalize an SDK usage object (or dict) to a plain dict of the fields we bill on."""
    out: dict[str, Any] = {
        k: _get(usage, k)
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens")
    }
    stu = usage.get("server_tool_use") if isinstance(usage, dict) else getattr(
        usage, "server_tool_use", None)
    if stu:   # server tools billed per use (web search)
        out["web_search_requests"] = _get(stu, "web_search_requests")
    creation = usage.get("cache_creation") if isinstance(usage, dict) else getattr(
        usage, "cache_creation", None)
    if creation:
        out["cache_creation"] = {k: _get(creation, k)
                                 for k in ("ephemeral_5m_input_tokens",
                                           "ephemeral_1h_input_tokens")}
    return out


def usage_cost(model: str, usage: Any) -> float:
    """USD cost of one response's `usage`.

    `input_tokens` excludes cached tokens; cache writes are split by TTL when the API reports
    `cache_creation`, else billed at the 5-minute rate.
    """
    try:
        p = office()["pricing"][model]
    except KeyError as e:
        raise KeyError(f"No pricing for model {model!r} in config/office.yaml") from e

    u = usage_dict(usage)
    cost = u["input_tokens"] * p["input"]
    cost += u["output_tokens"] * p["output"]
    cost += u["cache_read_input_tokens"] * p["cache_read"]
    creation = u.get("cache_creation")
    if creation:
        cost += creation["ephemeral_5m_input_tokens"] * p["cache_write_5m"]
        cost += creation["ephemeral_1h_input_tokens"] * p["cache_write_1h"]
    else:
        cost += u["cache_creation_input_tokens"] * p["cache_write_5m"]
    token_cost = cost / PER_MTOK
    # Web search is billed per search on top of tokens.
    per_1k = office().get("pricing_server_tools", {}).get("web_search_per_1k", 10.0)
    return token_cost + u.get("web_search_requests", 0) * per_1k / 1000


class BudgetExhausted(Exception):
    """Raised before a model call when the daily cap leaves no room for it."""


class Ledger:
    def __init__(self, store: Store, *, daily_cap: float | None = None,
                 audit_reserve: float | None = None, tz: str | None = None):
        cfg = office()["budget"]
        self.store = store
        self.daily_cap = cfg["daily_cap_usd"] if daily_cap is None else daily_cap
        self.audit_reserve = cfg["audit_reserve_usd"] if audit_reserve is None else audit_reserve
        self.tz = ZoneInfo(tz or cfg["timezone"])

    def today(self) -> str:
        return datetime.now(self.tz).date().isoformat()

    def spent_today(self) -> float:
        return self.store.spend_for_day(self.today())

    def remaining_today(self) -> float:
        return max(0.0, self.daily_cap - self.spent_today())

    def check(self, *, is_audit: bool = False) -> None:
        """Gate for a new model call. Everyone stops at the cap minus the audit reserve;
        audit agents may spend the reserve."""
        spent = self.spent_today()
        limit = self.daily_cap if is_audit else self.daily_cap - self.audit_reserve
        if spent >= limit:
            raise BudgetExhausted(
                f"Daily budget reached: ${spent:.2f} spent of ${self.daily_cap:.2f}"
                + ("" if is_audit else f" (${self.audit_reserve:.2f} held for Audit)"))

    def record(self, *, agent: str, task_id: int | None, root_id: int | None, model: str,
               usage: Any, request_id: str | None = None) -> float:
        cost = usage_cost(model, usage)
        self.store.add_spend(day=self.today(), agent=agent, task_id=task_id, root_id=root_id,
                             model=model, usage=usage_dict(usage), cost=cost,
                             request_id=request_id)
        return cost
