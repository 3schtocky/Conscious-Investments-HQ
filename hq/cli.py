"""`hq` command line."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import textwrap

from hq.config import model_config


def _need_key() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return False
    print("ANTHROPIC_API_KEY is not set. Add it to .env (see .env.example).", file=sys.stderr)
    return True


def smoke(tier: str) -> int:
    """One tiny API call to confirm the key, the model id and the cost math."""
    if _need_key():
        return 1
    import anthropic

    from hq.engine.ledger import usage_cost

    model = model_config(tier)[1]["id"]
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


def _fmt(office, ev) -> str | None:
    who = office.agents[ev.agent].nickname if ev.agent in office.agents else (ev.agent or "office")
    p = ev.payload
    wrap = lambda s: textwrap.indent(textwrap.fill(s, 96, replace_whitespace=False), "    ")
    match ev.type:
        case "task_started":
            return f"\n▶ {who} starts task #{ev.task_id} ({p['kind']}): {p['title']}"
        case "thinking":
            return f"  💭 {who} thinking:\n{wrap(p['text'])}"
        case "text":
            return f"  💬 {who}:\n{wrap(p['text'])}"
        case "tool_call":
            return f"  🔧 {who} → {p['tool']}({_short(p['input'])})"
        case "tool_result":
            flag = "⚠️ " if p["is_error"] else ""
            return f"  ↩ {flag}{p['tool']}: {_short(p['text'])}"
        case "chat":
            to = ", ".join(office.name(r) for r in p["recipients"])
            return f"  ✉️  {who} → {to}: {p['text']}"
        case "delegated":
            return f"  🤝 {who} delegates to {office.name(p['to'])} (task #{p['task']})"
        case "captain_report":
            return f"  📣 {who} → Captain:\n{wrap(p['text'])}"
        case "spend":
            u = p["usage"]
            return (f"  $ {who} {p['model']}: ${p['cost']:.4f} (in {u['input_tokens']}, "
                    f"cached {u['cache_read_input_tokens']}, out {u['output_tokens']}) · "
                    f"today ${p['spent_today']:.4f} / ${p['cap']:.2f}")
        case "task_done":
            return f"✔ {who} finished task #{ev.task_id}"
        case "task_paused" | "incident" | "task_error" | "guard_block" | "office_status":
            return f"  ⛔ {ev.type} {who}: {p}"
    return None


def _short(val, n: int = 160) -> str:
    s = val if isinstance(val, str) else repr(val)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


async def _run_agent(agent: str, body: str, title: str | None) -> int:
    from hq.engine.runtime import Office

    office = Office()
    q = office.bus.subscribe()

    async def printer():
        while True:
            line = _fmt(office, await q.get())
            if line:
                print(line, flush=True)

    printer_task = asyncio.create_task(printer())
    start = office.ledger.spent_today()
    task_id = office.assign(agent, body, title=title)
    await office.idle()
    await asyncio.sleep(0.05)
    printer_task.cancel()
    t = office.store.task(task_id)
    print(f"\nTask #{task_id}: {t['status']}  ·  this run cost "
          f"${office.ledger.spent_today() - start:.4f}  ·  "
          f"today ${office.ledger.spent_today():.4f} of ${office.ledger.daily_cap:.2f}")
    return 0 if t["status"] == "done" else 2


def spend() -> int:
    from hq.config import DATA_DIR
    from hq.engine.ledger import Ledger
    from hq.store import Store

    store = Store(DATA_DIR / "office.db")
    ledger = Ledger(store)
    day = ledger.today()
    print(f"{day}: ${ledger.spent_today():.4f} of ${ledger.daily_cap:.2f}")
    for r in store.spend_breakdown(day):
        print(f"  {r['agent']:<18} {r['model']:<20} {r['calls']:>3} calls  in {r['input_tokens']:>8}"
              f"  cached {r['cache_read_tokens']:>8}  out {r['output_tokens']:>7}  ${r['cost']:.4f}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="hq", description="Conscious Investments HQ")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_smoke = sub.add_parser("smoke", help="one tiny API call to check the key and pricing")
    p_smoke.add_argument("--tier", default="lead", help="model tier (lead|associate) or model id")
    p_run = sub.add_parser("run-agent", help="assign a task to one agent and watch the office")
    p_run.add_argument("agent", help="agent id or nickname, e.g. Quill")
    p_run.add_argument("task", help="what you want done")
    p_run.add_argument("--title")
    sub.add_parser("spend", help="today's spend by agent and model")
    p_serve = sub.add_parser("serve", help="open the office in your browser")
    p_serve.add_argument("--demo", action="store_true",
                         help="scripted demo office: zero API cost, separate database")
    p_serve.add_argument("--port", type=int, default=8750)
    p_serve.add_argument("--speed", type=float, default=1.0, help="demo playback speed")
    args = parser.parse_args()
    if args.cmd == "smoke":
        sys.exit(smoke(args.tier))
    if args.cmd == "run-agent":
        if _need_key():
            sys.exit(1)
        sys.exit(asyncio.run(_run_agent(args.agent, args.task, args.title)))
    if args.cmd == "spend":
        sys.exit(spend())
    if args.cmd == "serve":
        from hq.server import serve

        print(f"Conscious Investments HQ{' (demo)' if args.demo else ''}: "
              f"http://127.0.0.1:{args.port}")
        serve(demo=args.demo, port=args.port, speed=args.speed)
