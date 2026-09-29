"""Spend accounting. Cost of one API response, from its usage block and office.yaml pricing."""

from __future__ import annotations

from typing import Any

from hq.config import office

PER_MTOK = 1_000_000


def usage_cost(model: str, usage: Any) -> float:
    """USD cost of one response's `usage`.

    `input_tokens` excludes cached tokens; cache writes are split by TTL when the API reports
    `cache_creation`, else billed at the 5-minute rate.
    """
    try:
        p = office()["pricing"][model]
    except KeyError as e:
        raise KeyError(f"No pricing for model {model!r} in config/office.yaml") from e

    def get(obj: Any, name: str) -> int:
        val = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        return int(val or 0)

    cost = get(usage, "input_tokens") * p["input"]
    cost += get(usage, "output_tokens") * p["output"]
    cost += get(usage, "cache_read_input_tokens") * p["cache_read"]

    creation = usage.get("cache_creation") if isinstance(usage, dict) else getattr(
        usage, "cache_creation", None)
    if creation:
        cost += get(creation, "ephemeral_5m_input_tokens") * p["cache_write_5m"]
        cost += get(creation, "ephemeral_1h_input_tokens") * p["cache_write_1h"]
    else:
        cost += get(usage, "cache_creation_input_tokens") * p["cache_write_5m"]
    return cost / PER_MTOK
