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
    from hq.engine.llm import ApiDisabled, require_api

    try:
        require_api()
    except ApiDisabled as e:
        print(e, file=sys.stderr)
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
          f"cost=${usage_cost(model, msg.usage):.2f}  request_id={msg._request_id}")
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
            return (f"  $ {who} {p['model']}: ${p['cost']:.2f} (in {u['input_tokens']}, "
                    f"cached {u['cache_read_input_tokens']}, out {u['output_tokens']}) · "
                    f"today ${p['spent_today']:.2f} / ${p['cap']:.2f}")
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
          f"${office.ledger.spent_today() - start:.2f}  ·  "
          f"today ${office.ledger.spent_today():.2f} of ${office.ledger.daily_cap:.2f}")
    return 0 if t["status"] == "done" else 2


def spend() -> int:
    from hq.config import DATA_DIR
    from hq.engine.ledger import Ledger
    from hq.store import Store

    store = Store(DATA_DIR / "office.db")
    ledger = Ledger(store)
    day = ledger.today()
    print(f"{day}: ${ledger.spent_today():.2f} of ${ledger.daily_cap:.2f}")
    for r in store.spend_breakdown(day):
        print(f"  {r['agent']:<18} {r['model']:<20} {r['calls']:>3} calls  in {r['input_tokens']:>8}"
              f"  cached {r['cache_read_tokens']:>8}  out {r['output_tokens']:>7}  ${r['cost']:.2f}")
    return 0


def captain_password() -> int:
    """Generate a strong password, store it in .env and show it once, here in the terminal."""
    import re
    import secrets
    import tempfile

    from hq.config import ROOT
    from hq.public import PASSWORD_ENV

    env = ROOT / ".env"
    old = env.read_text().splitlines() if env.is_file() else []
    before = os.environ.get(PASSWORD_ENV)
    in_file = any(re.match(rf"\s*(?:export\s+)?{PASSWORD_ENV}\s*=", ln) for ln in old)
    kept = [ln for ln in old if not re.match(rf"\s*(?:export\s+)?{PASSWORD_ENV}\s*=", ln)]
    value = secrets.token_urlsafe(24)
    # Written whole to a private temp file and swapped in: .env also holds the API key, so it is
    # never left half-written or readable by other users.
    fd, tmp = tempfile.mkstemp(dir=str(ROOT), prefix=".env.")
    with os.fdopen(fd, "w") as f:          # mkstemp creates the file with mode 0600
        f.write("\n".join([*kept, f"{PASSWORD_ENV}={value}"]) + "\n")
    os.replace(tmp, env)
    print("The Captain's password for the public site (saved in .env, shown only here):\n")
    print(f"    {value}\n")
    print("Keep it in your password manager. Run this command again to replace it; that signs "
          "out every session once the office restarts.")
    if before and not in_file:   # set in the shell, where it would win over .env
        print(f"\nNote: {PASSWORD_ENV} is also set in your shell, and the shell's value wins over "
              f".env. Run `unset {PASSWORD_ENV}` before starting the office.", file=sys.stderr)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="hq", description="Conscious Investments HQ")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_smoke = sub.add_parser("smoke", help="one tiny API call to check the key and pricing")
    p_smoke.add_argument("--tier", default="lead", help="model tier (lead|associate) or model id")
    p_run = sub.add_parser("run-agent", help="assign a task to one agent and watch the office")
    p_run.add_argument("agent", help="agent role id, e.g. er_lead (a nickname also works)")
    p_run.add_argument("task", help="what you want done")
    p_run.add_argument("--title")
    sub.add_parser("spend", help="today's spend by agent and model")
    sub.add_parser("captain-password", help="create the Captain's sign-in password for public mode")
    p_exp = sub.add_parser("export-static", help="write the public views as JSON for the Cloudflare Pages site")
    p_exp.add_argument("--db", help="database to export (default data/office.db)")
    p_exp.add_argument("--out", help="output folder (default web/public/data)")
    p_exp.add_argument("--outbox", help="outbox folder holding approved issues")
    p_exp.add_argument("--max-events", type=int, default=400, help="replay length")
    p_exp.add_argument("--showcase", action="store_true",
                       help="first record every demo scene into a fresh database, then export that")
    p_exp.add_argument("--offline", action="store_true", help="skip live price lookups")
    sub.add_parser("go-live", help="checklist: what is ready for the public site, and what to do next")
    p_reh = sub.add_parser("rehearse", help="try the public site on your phone through a throwaway "
                                            "Cloudflare address, with the scripted demo office")
    p_reh.add_argument("--port", type=int, default=8753)
    p_reh.add_argument("--speed", type=float, default=1.0)
    p_svc = sub.add_parser("service-files", help="write the launch files (macOS) or PowerShell installer "
                                                 "(Windows) that keep the office and tunnel running; "
                                                 "nothing is installed")
    p_svc.add_argument("--port", type=int, default=8750)
    p_svc.add_argument("--tunnel", default="conscious-hq", help="Cloudflare tunnel name")
    p_serve = sub.add_parser("serve", help="open the office in your browser")
    p_serve.add_argument("--demo", action="store_true",
                         help="scripted demo office: zero API cost, separate database")
    p_serve.add_argument("--public", action="store_true",
                         help="for a public site behind a tunnel: visitors watch read-only, the "
                              "Captain signs in with the password in .env")
    p_serve.add_argument("--port", type=int, default=8750)
    p_serve.add_argument("--speed", type=float, default=1.0, help="demo playback speed")
    args = parser.parse_args()
    if args.cmd == "smoke":
        sys.exit(smoke(args.tier))
    if args.cmd == "run-agent":
        if _need_key():
            sys.exit(1)
        from hq.engine.llm import ApiDisabled, require_api

        try:
            require_api()
        except ApiDisabled as e:
            print(e, file=sys.stderr)
            sys.exit(1)
        sys.exit(asyncio.run(_run_agent(args.agent, args.task, args.title)))
    if args.cmd == "spend":
        sys.exit(spend())
    if args.cmd == "captain-password":
        sys.exit(captain_password())
    if args.cmd == "export-static":
        from pathlib import Path

        from hq import static_site

        db, outbox = Path(args.db) if args.db else None, Path(args.outbox) if args.outbox else None
        if args.showcase:
            db, outbox = static_site.build_showcase(), static_site.DATA_DIR / "showcase-outbox"
        static_site.export(db, Path(args.out) if args.out else None, outbox=outbox, offline=args.offline,
                           max_events=args.max_events)
        sys.exit(0)
    if args.cmd == "go-live":
        from hq import deploy

        text, ready = deploy.report()
        print(text)
        sys.exit(0 if ready else 1)
    if args.cmd == "rehearse":
        from hq import deploy

        sys.exit(deploy.rehearse(port=args.port, speed=args.speed))
    if args.cmd == "service-files":
        from hq import deploy

        print(deploy.write_service_files(port=args.port, tunnel=args.tunnel)[1])
        sys.exit(0)
    if args.cmd == "serve":
        from hq.server import serve

        print(f"Conscious Investments HQ{' (demo)' if args.demo else ''}: "
              f"http://127.0.0.1:{args.port}")
        serve(demo=args.demo, port=args.port, speed=args.speed, public=args.public)
