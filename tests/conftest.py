"""Shared fixtures: an Office wired to an in-memory store and a scripted fake model.

The fake picks each agent's next scripted turn by the nickname in its system prompt, so tests
read like a screenplay: {"Quill": [turn, turn], "Ledger": [turn]}.
"""

from __future__ import annotations

import copy
import itertools
import re
from collections import defaultdict

import pytest

from hq.engine.ledger import Ledger
from hq.engine.llm import TurnResult
from hq.engine.runtime import Office
from hq.store import Store

_ids = itertools.count(1)
USAGE = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0}


def text_turn(text: str, *, thinking: str | None = "Considering the task.",
              stop: str = "end_turn", usage: dict | None = None) -> TurnResult:
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": "sig"})
    content.append({"type": "text", "text": text})
    return TurnResult(content=content, stop_reason=stop, usage=usage or dict(USAGE),
                      model="fake", context_tokens=1000)


def tool_turn(*calls: tuple[str, dict], text: str | None = None,
              usage: dict | None = None) -> TurnResult:
    content = [{"type": "thinking", "thinking": "I'll use a tool.", "signature": "sig"}]
    if text:
        content.append({"type": "text", "text": text})
    for name, inp in calls:
        content.append({"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name,
                        "input": inp})
    return TurnResult(content=content, stop_reason="tool_use", usage=usage or dict(USAGE),
                      model="fake", context_tokens=1000)


class FakeLLM:
    def __init__(self, office_ref: dict):
        self.scripts: dict[str, list] = defaultdict(list)
        self.calls: list[dict] = []   # (nickname, params) for assertions
        self._office = office_ref

    def script(self, nickname: str, *turns) -> None:
        self.scripts[nickname].extend(turns)

    async def turn(self, *, params, on_delta=None, on_block=None) -> TurnResult:
        who = re.search(r"You are \*\*(.+?)\*\*", params["system"]).group(1)
        self.calls.append({"who": who, "params": copy.deepcopy(params)})
        if not self.scripts[who]:
            raise AssertionError(f"No scripted turn left for {who}")
        turn = self.scripts[who].pop(0)
        if callable(turn):
            turn = turn(params)
            if hasattr(turn, "__await__"):
                turn = await turn
        for block in turn.content:
            if on_delta and block["type"] in ("thinking", "text"):
                on_delta(block["type"], block.get(block["type"], ""))
            if on_block:
                on_block(block)
        return turn


@pytest.fixture(autouse=True)
def _never_spend(monkeypatch):
    """Tests never reach the real API, whatever config/office.yaml says: the switch is forced
    off, and the SDK is pointed at a dead local port as a second line of defence."""
    from hq import config

    real = config.office()
    safe = copy.deepcopy(real)
    safe.setdefault("api", {})["enabled"] = False
    monkeypatch.setattr(config, "office", lambda: safe)
    for mod in ("hq.engine.ledger", "hq.engine.agent", "hq.engine.runtime", "hq.engine.guards"):
        monkeypatch.setattr(f"{mod}.office", lambda: safe)
    # No test reaches Yahoo either: quotes come back empty unless a test supplies its own.
    monkeypatch.setattr("hq.quotes._download", lambda tickers: dict.fromkeys(tickers))
    monkeypatch.setattr("hq.quotes._cache", {})
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-valid")


@pytest.fixture
def make_office(monkeypatch, tmp_path):
    # Price the fake model like Sonnet so spend is non-zero and predictable.
    from hq import config

    patched = copy.deepcopy(config.office())   # already the API-off copy from _never_spend
    patched["pricing"]["fake"] = {"input": 2.0, "output": 10.0, "cache_read": 0.2,
                                  "cache_write_5m": 2.5, "cache_write_1h": 4.0}
    for tier in patched["models"].values():
        tier["id"] = "fake"
    monkeypatch.setattr(config, "office", lambda: patched)
    for mod in ("hq.engine.ledger", "hq.engine.agent", "hq.engine.runtime",
                "hq.engine.guards"):
        monkeypatch.setattr(f"{mod}.office", lambda: patched)

    def _make(daily_cap: float = 10.0, audit_reserve: float = 0.25):
        store = Store(":memory:")
        ref: dict = {}
        llm = FakeLLM(ref)
        ledger = Ledger(store, daily_cap=daily_cap, audit_reserve=audit_reserve)
        office_ = Office(store=store, llm=llm, ledger=ledger, quant_dir=tmp_path / "quant",
                         memory_dir=tmp_path / "memory", outbox_dir=tmp_path / "outbox")
        ref["office"] = office_
        return office_, llm

    return _make


# cost of one USAGE turn at the fake (Sonnet) price: 1000*2 + 200*10 per MTok = $0.004
TURN_COST = 0.004


def register_model(office, ticker: str = "RMBS", version: int = 1, ok: bool = True) -> None:
    """Put a checked model version in the registry, as build_model would."""
    office.store.add_model(ticker=ticker, version=version, path=f"/tmp/{ticker}_v{version}.xlsx",
                           created_by="quant_lead",
                           summary={"price_targets": {"bear": 1, "base": 2, "bull": 3},
                                    "total_returns": {}, "rating": "Neutral", "price": 2.0,
                                    "warnings": [], "as_of": "2026-09-30",
                                    "target_date": "2027-12-30", "check": {"ok": ok}})
