"""Cascade reporting: what each tier costs and how often the cheap model's first try had to be
polished by the expensive one. Read-only; the engine's escalation lives elsewhere."""

from __future__ import annotations

from hq.config import model_config


def tier_of(model: str) -> str:
    return model_config(model)[0]


def by_tier(store, day: str | None = None) -> dict:
    """Spend per tier, and per assignment: which tiers touched it and what it cost in each."""
    tiers: dict[str, dict] = {}
    roots: dict[int | None, dict] = {}
    for r in store.spend_by_root_model(day):
        tier = tier_of(r["model"])
        t = tiers.setdefault(tier, {"calls": 0, "cost": 0.0})
        t["calls"] += r["calls"]
        t["cost"] += r["cost"]
        roots.setdefault(r["root_id"], {})[tier] = roots.get(r["root_id"], {}).get(tier, 0.0) + r["cost"]
    return {"tiers": tiers, "roots": roots}


def summary(store, day: str | None = None) -> dict:
    """Cascade health over assignments that ran Haiku first (attempt 1 with a lead-tier polish
    behind it is an escalation). Returns counts and the share that escalated."""
    tasks = store.tasks()
    if day:   # tasks created that local day, matching the ledger's `day` column
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from hq.config import office
        tz = ZoneInfo(office()["budget"]["timezone"])
        tasks = [t for t in tasks if datetime.fromtimestamp(t["created"], tz).strftime("%Y-%m-%d") == day]
    tiers = _agent_tiers()
    started = [t for t in tasks if t["kind"] != "polish" and t["attempt"] == 1 and t["root_id"] == t["id"]
               and t["assignee"] != "chief_of_staff"   # Juno's routing is Haiku by design
               and tiers.get(t["assignee"], "lead") == "associate"]
    polished = {t["escalated_from"] for t in tasks if t["kind"] == "polish"}
    done = [t for t in started if t["status"] == "done"]
    escalated = [t for t in started if t["id"] in polished]
    reasons: dict[str, int] = {}
    for t in tasks:
        if t["kind"] == "polish" and t["escalation_reason"]:
            reasons[t["escalation_reason"]] = reasons.get(t["escalation_reason"], 0) + 1
    n = len(started)
    return {"haiku_first": n, "finished_on_haiku": len(done) - len([t for t in done if t in escalated]),
            "escalated": len(escalated), "escalation_rate": (len(escalated) / n) if n else 0.0,
            "reasons": reasons}


def _agent_tiers() -> dict[str, str]:
    from hq.config import roster
    return {a["id"]: model_config(a.get("model", "lead"))[0] for a in roster().get("agents", [])}
