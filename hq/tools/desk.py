"""Desk tools: the real work of the Equity Research and Quant wings.

Research runs erb's data tools and writes briefs and memos; Quant owns the assumptions, builds
the formula workbook, runs simulations and registers model versions for the Captain's approval.
Anyone can read the firm's approved model for a ticker.

Every file tool is confined to one ticker's folders:
  coverage/<TICKER>/   (Equity Research, inside the erb submodule)
  quant/<TICKER>/      (the Quant Department's models)
Nothing here calls the Anthropic API; erb's data tools use SEC EDGAR and Yahoo (free).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from hq.config import ERB_DIR
from hq.engine.guards import GuardBlock
from hq.tools.office import Tool, ToolContext, _str

TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
MAX_READ_LINES = 400
MAX_WRITE_CHARS = 60_000
ER_WRITABLE = re.compile(r"^(brief|sources|memo)\.md$|^notes/[\w\-]+\.md$")
SCREEN_WRITABLE = re.compile(r"^pitch\.md$|^notes/[\w\-]+\.md$")
PRESETS = ("gems", "core")
QUANT_WRITABLE = re.compile(r"^assumptions\.yaml$|^quant/notes/[\w\-]+\.md$")


def _ticker(inp: dict) -> str:
    t = str(inp.get("ticker", "")).strip().upper()
    if not TICKER.match(t):
        raise GuardBlock("`ticker` must be a US ticker like RMBS or BRK.B.")
    return t


def coverage_dir(ticker: str) -> Path:
    return ERB_DIR / "coverage" / ticker


def _resolve(ctx: ToolContext, ticker: str, rel: str) -> Path:
    """Map a path like 'brief.md', 'facts/facts.md' or 'quant/notes/x.md' into the ticker's
    folders and refuse anything that escapes them."""
    rel = rel.strip().lstrip("/")
    if rel.startswith("quant/") and ctx.agent.wing != "quant":
        raise GuardBlock("Quant's working files are private until a model is approved; read the "
                         "approved numbers with get_model.")
    if rel.startswith("quant/"):
        root = ctx.office.quant_dir / ticker
        target = (root / rel[len("quant/"):]).resolve()
    else:
        root = coverage_dir(ticker)
        target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()):
        raise GuardBlock("That path is outside this ticker's folders.")
    return target


async def run_erb(*args: str, timeout: float = 900) -> tuple[int, str]:
    """Run an erb command with this venv's Python, in the submodule. Returns (code, output)."""
    code = f"import sys; from erb.cli import main; main({list(args)!r})"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, cwd=str(ERB_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, f"erb {' '.join(args)} timed out after {timeout:.0f}s"
    text = out.decode(errors="replace")
    return proc.returncode or 0, text[-4000:]


def _erb_failure(what: str, out: str) -> str:
    """A readable failure: name the usual culprits instead of dumping a traceback."""
    if "RateLimit" in out or "Too Many Requests" in out or "429" in out:
        return (f"{what} failed: Yahoo Finance is rate-limiting us right now. Wait a few minutes "
                "and try again (nothing is wrong with the request).")
    last = [ln for ln in out.strip().splitlines() if ln.strip()][-3:]
    return f"{what} failed: " + " / ".join(last)[:600]


def _brief(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


# ---- files ----------------------------------------------------------------------------------
async def _list_files(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    lines = []
    for label, root in (("coverage", coverage_dir(t)), ("quant", ctx.office.quant_dir / t)):
        if not root.is_dir():
            lines.append(f"{label}/ (empty)")
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                rel = p.relative_to(root)
                prefix = "quant/" if label == "quant" else ""
                lines.append(f"{prefix}{rel}  ({p.stat().st_size:,} bytes)")
    return "\n".join(lines[:300]) or "No files yet."


async def _read_file(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    path = _resolve(ctx, t, _str(inp, "path", max_len=300))
    if not path.is_file():
        raise GuardBlock(f"No file {inp['path']!r} for {t}. Use list_files.")
    if path.suffix.lower() in (".xlsx", ".docx", ".pdf", ".png"):
        raise GuardBlock("That's a binary file; read the model with get_model or the .md/.csv/.yaml files.")
    start = max(int(inp.get("offset") or 0), 0)
    count = min(int(inp.get("lines") or 200), MAX_READ_LINES)
    lines = path.read_text(errors="replace").splitlines()
    chunk = lines[start:start + count]
    more = f"\n… {len(lines) - start - count} more lines (offset {start + count})" \
        if start + count < len(lines) else ""
    return "\n".join(f"{start + i + 1:>5}  {line}" for i, line in enumerate(chunk)) + more


async def _write_file(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    rel = _str(inp, "path", max_len=200).lstrip("/")
    content = inp.get("content")
    if not isinstance(content, str) or not content.strip():
        raise GuardBlock("`content` must be the full file text.")
    if len(content) > MAX_WRITE_CHARS:
        raise GuardBlock(f"Files are limited to {MAX_WRITE_CHARS:,} characters.")
    allowed, what = {
        "quant": (QUANT_WRITABLE, "assumptions.yaml or quant/notes/*.md"),
        "screening": (SCREEN_WRITABLE, "pitch.md or notes/*.md"),
    }.get(ctx.agent.wing, (ER_WRITABLE, "brief.md, sources.md, memo.md or notes/*.md"))
    if not allowed.match(rel):
        raise GuardBlock(f"Your wing can write {what}. The model and approved numbers belong to Quant.")
    path = _resolve(ctx, t, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    ctx.office.bus.publish("file_written", ctx.agent.id, ctx.task["id"], ticker=t, path=rel,
                           chars=len(content))
    return f"Saved {rel} for {t} ({len(content):,} characters)."


# ---- research (erb data) --------------------------------------------------------------------
async def _erb_facts(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    code, out = await run_erb("facts", t, *(["--no-filings"] if inp.get("skip_filings") else []))
    facts = coverage_dir(t) / "facts" / "facts.md"
    if code or not facts.is_file():
        raise GuardBlock(_erb_failure(f"erb facts for {t}", out))
    head = "\n".join(facts.read_text().splitlines()[:120])
    return (f"Facts pack ready: facts/facts.md (provenance in facts/provenance.csv, filing excerpts "
            f"in facts/filings/). First 120 lines:\n\n{head}")


async def _erb_peers(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    peers = inp.get("peers")
    if not isinstance(peers, list) or not 1 <= len(peers) <= 8:
        raise GuardBlock("`peers` must be a list of 1 to 8 tickers.")
    peers = [_ticker({"ticker": p}) for p in peers]
    code, out = await run_erb("peers", t, *peers)
    if code:
        raise GuardBlock(f"erb peers failed:\n{_brief(out, 1500)}")
    return _brief(out, 3500)


async def _erb_memo(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    code, out = await run_erb("memo", t)
    if code:
        raise GuardBlock(f"erb memo failed:\n{_brief(out, 1500)}")
    return f"{_brief(out, 1500)}\nThe memo skeleton is in coverage/{t}; read it with list_files/read_file."


async def _erb_lint(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    _, out = await run_erb("lint", t)
    return _brief(out, 3000)


# ---- quant -----------------------------------------------------------------------------------
async def _draft_assumptions(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    path = coverage_dir(t) / "assumptions.yaml"
    if path.exists() and not inp.get("overwrite"):
        return (f"assumptions.yaml already exists for {t}; read it with read_file and edit it with "
                "write_file (keep a `# why:` reason on every change). Pass overwrite=true to redraft.")
    code, out = await run_erb("model", t, "--init", *(["--force"] if inp.get("overwrite") else []))
    if code or not path.exists():
        raise GuardBlock(f"Drafting failed for {t} (run erb_facts first?):\n{_brief(out, 1500)}")
    return f"Drafted assumptions.yaml for {t} from the facts pack (starts at the Street).\n{_brief(out, 2500)}"


async def _build_model(ctx: ToolContext, inp: dict) -> str:
    from hq.quant.workbook import build_model

    t = _ticker(inp)
    src = coverage_dir(t) / "assumptions.yaml"
    if not src.exists():
        raise GuardBlock(f"No assumptions.yaml for {t}. Draft it with draft_assumptions first.")
    store = ctx.office.store
    by = ctx.office.agents[ctx.agent.id].nickname
    async with ctx.office.model_lock(t):   # one build per ticker at a time: versions never collide
        version = store.next_model_version(t)
        out = ctx.office.quant_dir / t / f"{t}_model_v{version}.xlsx"
        frozen = out.with_name(f"{t}_model_v{version}_assumptions.yaml")   # what this version used
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_text(src.read_text())
        result = await asyncio.to_thread(build_model, frozen, out, version=version,
                                         prepared_by=f"{by}, Quant")
        summary = {k: result[k] for k in ("price_targets", "total_returns", "rating", "price",
                                          "warnings", "as_of", "target_date")}
        summary["check"] = {"ok": result["ok"], "formulas": result["formulas"],
                            "compared": result["compared"], "problems": result["problems"]}
        summary["assumptions"] = str(frozen)
        store.add_model(ticker=t, version=version, path=str(out), created_by=ctx.agent.id,
                        summary=summary)
    ctx.office.bus.publish("model_built", ctx.agent.id, ctx.task["id"], ticker=t, version=version,
                           ok=result["ok"], path=str(out))
    status = ("Formula check PASSED: every output matches the valuation engine."
              if result["ok"] else "Formula check FAILED:\n- " + "\n- ".join(result["problems"][:10]))
    return json.dumps({"ticker": t, "version": version, "file": f"quant/{out.name}",
                       "price_targets": summary["price_targets"], "rating": summary["rating"],
                       "total_returns": summary["total_returns"], "price": summary["price"],
                       "warnings": summary["warnings"], "formulas": result["formulas"],
                       "check": status}, indent=1, default=float)


async def _run_simulations(ctx: ToolContext, inp: dict) -> str:
    from hq.quant.simulate import drivers, monte_carlo, write_to_workbook
    from hq.quant.workbook import load_assumptions

    t = _ticker(inp)
    versions = ctx.office.store.models(t)
    if not versions:
        raise GuardBlock(f"No model for {t} yet. Build it with build_model first.")
    m = versions[-1]
    if m["status"] not in ("draft", "changes"):
        raise GuardBlock(f"v{m['version']} is {m['status']}; its workbook is frozen. Build a new "
                         "version first, then simulate that.")
    frozen = Path(m["summary"].get("assumptions") or coverage_dir(t) / "assumptions.yaml")
    a, _ = load_assumptions(frozen)   # the inputs this version was built from
    runs = min(max(int(inp.get("runs") or 2000), 200), 10_000)
    mc = await asyncio.to_thread(monte_carlo, a, runs)
    drv = await asyncio.to_thread(drivers, a)
    await asyncio.to_thread(write_to_workbook, Path(m["path"]), mc, drv)
    pct = mc["percentiles"]
    return json.dumps({
        "ticker": t, "version": m["version"], "runs": mc["n"],
        "price_target_p10_p50_p90": [round(pct[10], 2), round(pct[50], 2), round(pct[90], 2)],
        "chance_above_price": round(mc["p_above_price"], 3),
        "chance_beats_sp500_benchmark": round(mc["p_beats_benchmark"], 3),
        "top_value_drivers": [{"driver": d["driver"], "pt_range": [round(d["pt_low"], 2),
                                                                   round(d["pt_high"], 2)]}
                              for d in drv[:4]],
        "note": "Written to the workbook's Monte Carlo and Value Drivers tabs.",
    }, indent=1)


async def _get_model(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    version = inp.get("version")
    store = ctx.office.store
    if version is None:
        m = store.approved_model(t)
        if m is None:
            drafts = store.models(t)
            hint = (f" Latest draft is v{drafts[-1]['version']} ({drafts[-1]['status']}); it is not "
                    "the firm's numbers until approved." if drafts else "")
            return f"No approved model for {t}.{hint} Ask Quant (Sigma) for one; never use unapproved figures."
    else:
        m = store.model(t, int(version))
        if m is None:
            raise GuardBlock(f"No model v{version} for {t}.")
        if ctx.agent.wing != "quant" and m["status"] not in ("approved", "superseded"):
            raise GuardBlock(f"v{version} is a {m['status']} draft: nothing from Quant reaches "
                             "other teams until the Captain approves it. Use get_model without a "
                             "version for the approved numbers.")
    official = m["status"] == "approved"
    s = m["summary"]
    from datetime import datetime
    approved = (datetime.fromtimestamp(m["approved_at"], ctx.office.ledger.tz).date().isoformat()
                if m["approved_at"] else None)
    return json.dumps({
        "ticker": t, "version": m["version"], "status": m["status"], "official": official,
        "approved_on": approved, "price_targets": s["price_targets"], "rating": s["rating"],
        "total_returns": s["total_returns"], "price_at_build": s["price"], "as_of": s["as_of"],
        "target_date": s["target_date"], "file": f"quant/{Path(m['path']).name}",
        "cite_as": (f"Quant model v{m['version']}, approved {approved}" if official
                    else f"DRAFT model v{m['version']}: not approved, do not publish"),
    }, indent=1, default=float)


# ---- screening ------------------------------------------------------------------------------
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
    head = "\n".join(dest.read_text().splitlines()[:60])
    return (f"Pitch skeleton for {t} saved as pitch.md (numbers auto-filled from filings and the screen; "
            f"no valuation, which is Quant's job). Fill every [VERIFY: ...] with write_file, keeping it to "
            f"one page.\n\n{head}")


async def _add_to_watchlist(ctx: ToolContext, inp: dict) -> str:
    from hq import quotes

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


# ---- definitions ------------------------------------------------------------------------------
def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


TICK = {"ticker": {"type": "string", "description": "US ticker, e.g. RMBS"}}

LIST_FILES = Tool("list_files", "List the files for a ticker: coverage/ (research) and quant/ (models).",
                  _schema(TICK, ["ticker"]), _list_files)
READ_FILE = Tool(
    "read_file", "Read a text file for a ticker (facts/facts.md, brief.md, assumptions.yaml, "
    "sources.md, quant/notes/...). Returns numbered lines; page with offset.",
    _schema({**TICK, "path": {"type": "string"}, "offset": {"type": "integer"},
             "lines": {"type": "integer", "description": f"max {MAX_READ_LINES}"}}, ["ticker", "path"]),
    _read_file)
WRITE_FILE = Tool(
    "write_file", "Write a whole text file for a ticker. Research: brief.md, sources.md, memo.md, "
    "notes/*.md. Quant: assumptions.yaml (keep a `# why:` reason on every change), quant/notes/*.md.",
    _schema({**TICK, "path": {"type": "string"}, "content": {"type": "string"}},
            ["ticker", "path", "content"]), _write_file)
ERB_FACTS = Tool(
    "erb_facts", "Build the facts pack for a ticker from SEC EDGAR filings and market data "
    "(financials, segments, filing excerpts, multiples history, consensus). Start here.",
    _schema({**TICK, "skip_filings": {"type": "boolean"}}, ["ticker"]), _erb_facts)
ERB_PEERS = Tool("erb_peers", "Market snapshot for a ticker and its peers (multiples side by side).",
                 _schema({**TICK, "peers": {"type": "array", "items": {"type": "string"}}},
                         ["ticker", "peers"]), _erb_peers)
ERB_MEMO = Tool("erb_memo", "Write a one-page pitch memo skeleton for a ticker (snapshot, valuation vs "
                "history, Street view). Fill its narrative after research.",
                _schema(TICK, ["ticker"]), _erb_memo)
ERB_LINT = Tool("erb_lint", "Style and sourcing checks on the ticker's written sections.",
                _schema(TICK, ["ticker"]), _erb_lint)
DRAFT_ASSUMPTIONS = Tool(
    "draft_assumptions", "Draft assumptions.yaml from the facts pack (calibrated to the Street). "
    "Then edit it with write_file, with a reason on every change.",
    _schema({**TICK, "overwrite": {"type": "boolean"}}, ["ticker"]), _draft_assumptions)
BUILD_MODEL = Tool(
    "build_model", "Build the next version of the Excel model from assumptions.yaml: Inputs, "
    "calculation tabs per scenario and Outputs, all live formulas, then check every output "
    "against the valuation engine. Registers the version as a draft.",
    _schema(TICK, ["ticker"]), _build_model)
RUN_SIMULATIONS = Tool(
    "run_simulations", "Monte Carlo on the latest model version plus one-at-a-time value drivers; "
    "results go into the workbook and come back as a summary.",
    _schema({**TICK, "runs": {"type": "integer", "description": "200 to 10,000 (default 2,000)"}},
            ["ticker"]), _run_simulations)
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
GET_MODEL = Tool(
    "get_model", "The firm's approved model for a ticker (price targets, rating, version, approval "
    "date), or a specific version. Only approved numbers may appear in anything published.",
    _schema({**TICK, "version": {"type": "integer"}}, ["ticker"]), _get_model)


def desk_tools(wing: str, tier: str) -> list[Tool]:
    """Work tools by wing. Everyone can read the approved model."""
    if wing == "equity_research":
        tools = [ERB_FACTS, ERB_PEERS, LIST_FILES, READ_FILE, WRITE_FILE, GET_MODEL]
        if tier == "lead":
            tools += [ERB_MEMO, ERB_LINT]
        return tools
    if wing == "quant":
        return [LIST_FILES, READ_FILE, WRITE_FILE, DRAFT_ASSUMPTIONS, BUILD_MODEL, RUN_SIMULATIONS,
                GET_MODEL]
    if wing == "screening":
        tools = [READ_SCREEN, PITCH_MEMO, LIST_FILES, READ_FILE, WRITE_FILE, GET_MODEL]
        if tier == "lead":
            tools = [RUN_SCREEN, *tools, ADD_TO_WATCHLIST]
        return tools
    return [GET_MODEL]
