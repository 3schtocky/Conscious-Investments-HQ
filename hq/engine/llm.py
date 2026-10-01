"""Thin adapter over the Anthropic SDK: one streamed model turn in, one `TurnResult` out.

The agent loop only talks to `ModelClient.turn()`, so tests can swap in a scripted fake and the
engine never depends on SDK object shapes. Content comes back as plain dicts that are replayed
unchanged on the next turn (Sonnet 5.5 binds thinking blocks to the exact conversation).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from hq.engine.ledger import usage_dict

# Content blocks are replayed exactly as the API returned them (Sonnet 5.5 binds thinking to the
# conversation, and server tools link blocks through fields like `caller`). Only SDK-side extras
# are dropped: None values and parsed_* helpers.
OnDelta = Callable[[str, str], Awaitable[None] | None]   # (kind, text) with kind thinking|text
OnBlock = Callable[[dict], Awaitable[None] | None]        # a completed content block


@dataclass
class TurnResult:
    content: list[dict]
    stop_reason: str | None
    usage: dict[str, Any]
    model: str
    request_id: str | None = None
    stop_details: dict | None = None
    context_tokens: int = field(default=0)   # total prompt size this turn, for the context guard


class ModelClient(Protocol):
    async def turn(self, *, params: dict, on_delta: OnDelta | None = None,
                   on_block: OnBlock | None = None) -> TurnResult: ...


def block_to_param(block: Any) -> dict:
    """SDK content block (or dict) -> a dict safe to send back to the API unchanged."""
    raw = block if isinstance(block, dict) else block.model_dump(mode="json", exclude_none=True)
    return {k: v for k, v in raw.items() if v is not None and not k.startswith("parsed")}


def request_params(model_cfg: dict, *, system: str, messages: list[dict],
                   tools: list[dict]) -> dict:
    """Build messages.stream kwargs for one agent turn, per model family."""
    model = model_cfg["id"]
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": model_cfg.get("max_tokens", 16000),
        "system": system,
        "messages": messages,
        # Automatic prompt caching: the stable system prompt, tools and history prefix.
        "cache_control": {"type": "ephemeral"},
    }
    if tools:
        params["tools"] = tools
    if model.startswith("claude-haiku-4-5"):
        # Haiku 4.5: manual thinking budget, no effort parameter.
        budget = int(model_cfg.get("thinking_budget_tokens", 0))
        if budget:
            params["thinking"] = {"type": "enabled", "budget_tokens": max(budget, 1024)}
    else:
        # Sonnet 5.5 / Opus: adaptive thinking; summaries make the reasoning visible in the UI.
        params["thinking"] = {"type": "adaptive",
                              "display": model_cfg.get("thinking_display", "summarized")}
        if model_cfg.get("effort"):
            params["output_config"] = {"effort": model_cfg["effort"]}
    return params


WEB_WINGS = {"equity_research"}   # wings that research the open web
WEB_AGENTS = {"screen_associate"}  # plus these individuals (Pip digs catalysts on finalists)
WEB_SEARCH_MAX_USES = 5            # per request: web search is billed per search


def web_tools(model_cfg: dict, wing: str, agent_id: str = "") -> list[dict]:
    """Anthropic's server-side web search and fetch for research wings (real office only; the
    demo and tests never execute them). Newer models get the dynamic-filtering versions."""
    if wing not in WEB_WINGS and agent_id not in WEB_AGENTS:
        return []
    new = not str(model_cfg.get("id", "")).startswith("claude-haiku-4-5")
    return [
        {"type": "web_search_20260209" if new else "web_search_20250305", "name": "web_search",
         "max_uses": WEB_SEARCH_MAX_USES},
        {"type": "web_fetch_20260209" if new else "web_fetch_20250910", "name": "web_fetch",
         "max_uses": WEB_SEARCH_MAX_USES},
    ]


async def _call(fn: Callable | None, *args: Any) -> None:
    if fn is None:
        return
    res = fn(*args)
    if hasattr(res, "__await__"):
        await res


class ApiDisabled(RuntimeError):
    """Raised instead of any real API call while `api.enabled` is false in office.yaml."""


def require_api() -> None:
    """Gate for every real Anthropic API call. Off by default so nothing spends by accident."""
    from hq.config import office

    if not office().get("api", {}).get("enabled", False):
        raise ApiDisabled(
            "The Anthropic API is switched off (api.enabled: false in config/office.yaml), so "
            "no credits were spent. Use `hq serve --demo` for free runs; Stott turns the API "
            "on only for an approved real run.")


class AnthropicClient:
    """Real client: AsyncAnthropic streaming. Every call passes `require_api()` first."""

    def __init__(self, client: Any | None = None):
        require_api()
        if client is None:
            import anthropic
            client = anthropic.AsyncAnthropic()
        self._client = client

    async def turn(self, *, params: dict, on_delta: OnDelta | None = None,
                   on_block: OnBlock | None = None) -> TurnResult:
        require_api()   # re-checked per call: switching the API off takes effect immediately
        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    d = event.delta
                    if d.type == "thinking_delta" and d.thinking:
                        await _call(on_delta, "thinking", d.thinking)
                    elif d.type == "text_delta" and d.text:
                        await _call(on_delta, "text", d.text)
                elif event.type == "content_block_stop":
                    block = getattr(event, "content_block", None)
                    if block is not None:
                        await _call(on_block, block_to_param(block))
            msg = await stream.get_final_message()
        usage = usage_dict(msg.usage)
        stop_details = None
        if msg.stop_reason == "refusal" and getattr(msg, "stop_details", None):
            stop_details = msg.stop_details.model_dump(mode="json", exclude_none=True)
        return TurnResult(
            content=[block_to_param(b) for b in msg.content],
            stop_reason=msg.stop_reason,
            usage=usage,
            model=msg.model,
            request_id=getattr(msg, "_request_id", None),
            stop_details=stop_details,
            context_tokens=usage["input_tokens"] + usage["cache_read_input_tokens"]
            + usage["cache_creation_input_tokens"],
        )
