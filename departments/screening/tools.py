"""Screening tools: run and read erb screens, write pitch memos, add names to the watchlist."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from HQ.config import ERB_DIR
from HQ.engine.guards import GuardBlock
from HQ.tools.desk import TICK, _erb_failure, _schema, _ticker, coverage_dir, run_erb
from HQ.tools.office import Tool, ToolContext, _str

PRESETS = ("gems", "core")


def screen_dir(preset: str, day: str | None = None) -> Path:
    from datetime import date as _date
    day = day or _date.today().isoformat()  # noqa: DTZ011 - matches erb's local-date folder names
    return ERB_DIR / "coverage" / "_screens" / (f"{day}-gems" if preset == "gems" else day)


def latest_screen_dir(preset: str) -> Path | None:
    root = ERB_DIR / "coverage" / "_screens"
    if not root.is_dir():
        return None
    runs = [p for p in root.iterdir() if (p / "screen.csv").exists()
            and (p.name.endswith("-gems") if preset == "gems" else re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))]
    return max(runs) if runs else None


def _preset(inp: dict) -> str:
    p = str(inp.get("preset", "gems")).lower()
    if p not in PRESETS:
        raise GuardBlock("`preset` must be gems or core.")
    return p


def screen_rows(run: Path, top: int) -> list[dict]:
    import pandas as pd

    df = pd.read_csv(run / "screen.csv")
    df = df[df["factors_used"] >= 3].sort_values("composite", ascending=False).head(top)
    gems = "acceleration" in df.columns
    cols = (["ticker", "name", "sector", "market_cap", "composite", "acceleration", "growth", "margin",
             "momentum", "yoy_q", "yoy_q1", "margin_change", "mom_6m", "runway_years", "loss_maker",
             "flags"] if gems else
            ["ticker", "name", "sector", "market_cap", "composite", "value", "quality", "growth", "momentum",
             "rev_growth", "op_margin", "mom_12_1"])
    rows = []
    for i, (_, r) in enumerate(df.iterrows(), 1):
        row = {"rank": i}
        for c in cols:
            v = r.get(c)
            row[c] = (None if pd.isna(v) else round(float(v), 4) if isinstance(v, float) else v)
        rows.append(row)
    return rows


async def _run_screen(ctx: ToolContext, inp: dict) -> str:
    preset = _preset(inp)
    run = screen_dir(preset)
    if not (run / "screen.csv").exists() or inp.get("refresh"):
        code, out = await run_erb("screen", "--preset", preset,
                                  *(["--refresh"] if inp.get("refresh") and preset == "gems" else []),
                                  timeout=2400)
        if code or not (run / "screen.csv").exists():
            raise GuardBlock(_erb_failure(f"The {preset} screen", out))
        ran = "Ran"
    else:
        ran = "Reused today's"
    ctx.office.bus.publish("screen_run", ctx.agent.id, ctx.task["id"], preset=preset, run=run.name)
    rows = screen_rows(run, 15)
    return json.dumps({"note": f"{ran} {preset} screen ({run.name}). Top 15 below; read_screen for more "
                               "and for 8-K events.", "top": rows}, indent=1, default=str)


async def _read_screen(ctx: ToolContext, inp: dict) -> str:
    preset = _preset(inp)
    run = latest_screen_dir(preset)
    if run is None:
        raise GuardBlock(f"No {preset} screen yet. Run it with run_screen.")
    top = min(max(int(inp.get("top") or 15), 1), 50)
    rows = screen_rows(run, top)
    signals = {}
    sig = run / "signals.json"
    if sig.exists():
        allsig = json.loads(sig.read_text())
        signals = {r["ticker"]: allsig.get(r["ticker"], []) for r in rows if allsig.get(r["ticker"])}
    return json.dumps({"run": run.name, "preset": preset, "rows": rows, "recent_8k_events": signals},
                      indent=1, default=str)


async def _pitch_memo(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    preset = _preset(inp)
    run = latest_screen_dir(preset)
    if run is None:
        raise GuardBlock(f"No {preset} screen yet; a pitch comes from a screened name. Run the screen first.")
    dest = coverage_dir(t) / "pitch.md"
    if dest.exists() and not inp.get("overwrite"):
        head = "\n".join(dest.read_text().splitlines()[:60])
        return (f"{t} already has a pitch.md (kept, so no work is lost). Edit it with write_file, or "
                f"pass overwrite=true for a fresh skeleton.\n\n{head}")
    code, out = await run_erb("memo", t, "--no-model", "--screen", str(run), timeout=900)
    src = run / "memos" / f"{t}.md"
    if code or not src.exists():
        raise GuardBlock(_erb_failure(f"erb memo for {t}", out))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text())
    ctx.office.bus.publish("file_written", ctx.agent.id, ctx.task["id"], ticker=t, path="pitch.md",
                           chars=len(dest.read_text()))
    head = "\n".join(dest.read_text().splitlines()[:60])
    return (f"Pitch skeleton for {t} saved as pitch.md (numbers auto-filled from filings and the screen; "
            f"no valuation, which is Quant's job). Fill every [VERIFY: ...] with write_file, keeping it to "
            f"one page.\n\n{head}")


async def _add_to_watchlist(ctx: ToolContext, inp: dict) -> str:
    from HQ import quotes

    t = _ticker(inp)
    thesis = _str(inp, "thesis", max_len=600)
    source = _str(inp, "source", max_len=80)
    pitch = "pitch.md" if (coverage_dir(t) / "pitch.md").exists() else None
    px = await asyncio.to_thread(quotes.latest, [t, "SPY"])
    watch_id = ctx.office.add_watch(ticker=t, added_by=ctx.agent.id, source=source, thesis=thesis,
                                    pitch=pitch, price=px.get(t), spy=px.get("SPY"),
                                    task_id=ctx.task["id"])
    return (f"{t} is on the watchlist (#{watch_id}) at ${px.get(t) or 0:,.2f}. It stays there until "
            f"{ctx.office.captain_name} sends it to research.")


RUN_SCREEN = Tool(
    "run_screen", "Run today's screen: gems (small/mid caps at a growth inflection) or core (quality "
    "growth, $2B+). Free data (SEC, Yahoo) but takes several minutes; today's run is reused unless "
    "refresh=true. Returns the top 15.",
    _schema({"preset": {"type": "string", "enum": list(PRESETS)}, "refresh": {"type": "boolean"}},
            ["preset"]), _run_screen)


READ_SCREEN = Tool(
    "read_screen", "Read the latest gems or core screen: ranked rows with factor scores and, for "
    "Gems, recent 8-K material events for the finalists.",
    _schema({"preset": {"type": "string", "enum": list(PRESETS)},
             "top": {"type": "integer", "description": "1 to 50 (default 15)"}}, ["preset"]), _read_screen)


PITCH_MEMO = Tool(
    "pitch_memo", "Write a one-page pitch memo skeleton for a screened ticker (snapshot, why it "
    "screened, multiples vs history, Street view; no valuation) as pitch.md, then fill its "
    "[VERIFY] sections with write_file.",
    _schema({**TICK, "preset": {"type": "string", "enum": list(PRESETS)},
             "overwrite": {"type": "boolean", "description": "replace an existing pitch.md"}},
            ["ticker"]), _pitch_memo)


ADD_TO_WATCHLIST = Tool(
    "add_to_watchlist", "Put a shortlisted name on the Captain's watchlist with a one or two line "
    "thesis and its source (e.g. \"gems 2026-09-30 #3\"). Its price today is recorded so the "
    "office can track how the pick does.",
    _schema({**TICK, "thesis": {"type": "string"}, "source": {"type": "string"}},
            ["ticker", "thesis", "source"]), _add_to_watchlist)

