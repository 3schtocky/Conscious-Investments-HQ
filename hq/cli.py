"""`hq` command line."""

from __future__ import annotations

import argparse
import os
import sys

from hq.config import model_config
from hq.engine.ledger import usage_cost


def smoke(tier: str) -> int:
    """One tiny API call to confirm the key, the model id and the cost math."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example).", file=sys.stderr)
        return 1
    import anthropic

    model = model_config(tier)["id"]
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=64,
        messages=[{"role": "user", "content": "Reply with exactly: Conscious Investments HQ is open."}],
    )
    text = next((b.text for b in msg.content if b.type == "text"), "")
    print(f"{model}: {text.strip()}")
    print(f"usage: in={msg.usage.input_tokens} out={msg.usage.output_tokens} "
          f"cost=${usage_cost(model, msg.usage):.5f}  request_id={msg._request_id}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="hq", description="Conscious Investments HQ")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_smoke = sub.add_parser("smoke", help="one tiny API call to check the key and pricing")
    p_smoke.add_argument("--tier", default="lead", help="model tier (lead|associate) or model id")
    args = parser.parse_args()
    if args.cmd == "smoke":
        sys.exit(smoke(args.tier))
