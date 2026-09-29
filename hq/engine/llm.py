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

# Keys the API accepts back for each content block type. Anything else the SDK adds
# (parsed output, None-valued extras) is dropped before replay.
_REPLAY_KEYS = {
    "text": ("type", "text", "citations"),
    "thinking": ("type", "thinking", "signature"),
    "redacted_thinking": ("type", "data"),
    "tool_use": ("type", "id", "name", "input"),
    "server_tool_use": ("type", "id", "name", "input"),
}

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
    keys = _REPLAY_KEYS.get(raw.get("type"))
    if keys is None:   # server tool results and future block types: keep as dumped
        return {k: v for k, v in raw.items() if v is not None and not k.startswith("parsed")}
    return {k: raw[k] for k in keys if k in raw and raw[k] is not None}


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


async def _call(fn: Callable | None, *args: Any) -> None:
    if fn is None:
        return
    res = fn(*args)
    if hasattr(res, "__await__"):
        await res


class AnthropicClient:
    """Real client: AsyncAnthropic streaming."""

    def __init__(self, client: Any | None = None):
        if client is None:
            import anthropic
            client = anthropic.AsyncAnthropic()
        self._client = client

    async def turn(self, *, params: dict, on_delta: OnDelta | None = None,
                   on_block: OnBlock | None = None) -> TurnResult:
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
