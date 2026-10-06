"""Desk tools shared by the wings that work on a ticker: file tools, `get_model`, and the helpers
the department tool modules build on.

Wing-specific tools live with their department: departments/equity_research/tools.py,
departments/quant/tools.py, departments/screening/tools.py. `desk_tools` assembles each wing's set.

Original overview:

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
import difflib
import json
import re
import sys
from pathlib import Path

from HQ.config import ERB_DIR
from HQ.engine.guards import GuardBlock
from HQ.tools.office import Tool, ToolContext, _str

TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
MAX_READ_LINES = 400
MAX_READ_CHARS = 20_000   # one read returns at most this much text: long filings eat context fast
BINARY = (".xlsx", ".docx", ".pdf", ".png")
SEARCH_HITS = 25
MAX_WRITE_CHARS = 60_000
ER_WRITABLE = re.compile(r"^(brief|sources|memo)\.md$|^notes/[\w\-]+\.md$")
SCREEN_WRITABLE = re.compile(r"^pitch\.md$|^notes/[\w\-]+\.md$")
QUANT_WRITABLE = re.compile(r"^assumptions\.yaml$|^quant/notes/[\w\-]+\.md$")


def _ticker(inp: dict) -> str:
    t = str(inp.get("ticker", "")).strip().upper()
    if not TICKER.match(t):
        raise GuardBlock("`ticker` must be a US ticker like RMBS or BRK.B.")
    return t


def coverage_dir(ticker: str) -> Path:
    return ERB_DIR / "coverage" / ticker


def _relative(ticker: str, rel: str) -> str:
    """Agents often write 'EOSE/facts/facts.md' or 'coverage/EOSE/facts/facts.md': both mean
    'facts/facts.md' inside the ticker's folder."""
    rel = rel.strip().lstrip("/")
    for prefix in (f"coverage/{ticker}/", f"{ticker}/", "coverage/"):
        if rel.startswith(prefix):
            return rel[len(prefix):]
    return rel


def _readable_files(ctx: ToolContext, ticker: str) -> list[str]:
    """Paths this agent can read for a ticker, as read_file wants them."""
    roots = [("", coverage_dir(ticker))]
    if ctx.agent.wing in ("quant", "audit"):
        roots.append(("quant/", ctx.office.quant_dir / ticker))
    names = []
    for prefix, root in roots:
        if root.is_dir():
            names += [prefix + p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))
                      if p.is_file() and not p.name.startswith(".")
                      and p.suffix.lower() not in BINARY]
    return names


def _resolve(ctx: ToolContext, ticker: str, rel: str) -> Path:
    """Map a path like 'brief.md', 'facts/facts.md' or 'quant/notes/x.md' into the ticker's
    folders and refuse anything that escapes them."""
    rel = _relative(ticker, rel)
    if rel.startswith("quant/") and ctx.agent.wing not in ("quant", "audit"):
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
    given = _str(inp, "path", max_len=300)
    path = _resolve(ctx, t, given)
    if not path.is_file():
        names = _readable_files(ctx, t)
        if not names:
            raise GuardBlock(f"No file {given!r}: there are no files for {t} yet (run erb_facts first).")
        close = difflib.get_close_matches(_relative(t, given), names, n=3, cutoff=0.5)
        raise GuardBlock(f"No file {given!r} for {t}."
                         + (f" Closest: {', '.join(close)}." if close else "")
                         + f" Files you can read: {', '.join(names[:40])}"
                         + (f" (and {len(names) - 40} more; use list_files)" if len(names) > 40 else ""))
    if path.suffix.lower() in BINARY:
        raise GuardBlock("That's a binary file; read the model with get_model or the .md/.csv/.yaml files.")
    start = max(int(inp.get("offset") or 0), 0)
    count = min(int(inp.get("lines") or 200), MAX_READ_LINES)
    lines = path.read_text(errors="replace").splitlines()
    out, used, shown = [], 0, 0
    for i, line in enumerate(lines[start:start + count]):
        row = f"{start + i + 1:>5}  {line}"
        if used + len(row) > MAX_READ_CHARS and out:
            break
        out.append(row)
        used += len(row) + 1
        shown += 1
    end = start + shown
    more = ""
    if end < len(lines):
        more = f"\n… {len(lines) - end} more lines (continue at offset {end})"
        if end < start + count:
            more += f"; this read stopped at {MAX_READ_CHARS:,} characters"
    return "\n".join(out) + more


async def _search_file(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    pattern = _str(inp, "pattern", max_len=200)
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:   # plain words with brackets or stars in them: match them literally
        rx = re.compile(re.escape(pattern), re.IGNORECASE)
    given = str(inp.get("path") or "").strip()
    if given:
        path = _resolve(ctx, t, given)
        if not path.is_file():
            raise GuardBlock(f"No file {given!r} for {t}. Leave `path` out to search every file, or "
                             "use list_files.")
        files = [_relative(t, given)]
    else:
        files = _readable_files(ctx, t)
    hits, total = [], 0
    for rel in files:
        text = _resolve(ctx, t, rel).read_text(errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                total += 1
                if len(hits) < SEARCH_HITS:
                    hits.append(f"{rel}:{n}: {line.strip()[:300]}")
    if not hits:
        return f"No lines match {pattern!r} in {len(files)} file(s) for {t}. Try a shorter or different word."
    more = f"\n… {total - len(hits)} more matches; use a narrower pattern or name a path." \
        if total > len(hits) else ""
    return ("\n".join(hits) + more + "\nRead around a hit with read_file (offset = line minus 5).")


async def _write_file(ctx: ToolContext, inp: dict) -> str:
    t = _ticker(inp)
    rel = _relative(t, _str(inp, "path", max_len=200))
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
            return f"No approved model for {t}.{hint} Ask Quant for one; never use unapproved figures."
    else:
        m = store.model(t, int(version))
        if m is None:
            raise GuardBlock(f"No model v{version} for {t}.")
        if ctx.agent.wing not in ("quant", "audit") and m["status"] not in ("approved", "superseded"):
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
SEARCH_FILE = Tool(
    "search_file", "Find lines mentioning a word or phrase (case-insensitive; a regex works too) in one "
    "file or, with no path, in every file for the ticker. Returns file:line: text. Far cheaper than "
    "paging through a long filing: search first, then read_file around the hit.",
    _schema({**TICK, "pattern": {"type": "string"},
             "path": {"type": "string", "description": "Optional: one file, e.g. facts/filings/10-K_..."}},
            ["ticker", "pattern"]), _search_file)
WRITE_FILE = Tool(
    "write_file", "Write a whole text file for a ticker. Research: brief.md, sources.md, memo.md, "
    "notes/*.md. Quant: assumptions.yaml (keep a `# why:` reason on every change), quant/notes/*.md.",
    _schema({**TICK, "path": {"type": "string"}, "content": {"type": "string"}},
            ["ticker", "path", "content"]), _write_file)
GET_MODEL = Tool(
    "get_model", "The firm's approved model for a ticker (price targets, rating, version, approval "
    "date), or a specific version. Only approved numbers may appear in anything published.",
    _schema({**TICK, "version": {"type": "integer"}}, ["ticker"]), _get_model)


def desk_tools(wing: str, tier: str) -> list[Tool]:
    """Work tools by wing. Everyone can read the approved model. The wing-specific tools live in
    each department's own folder (departments/<wing>/tools.py)."""
    files = [LIST_FILES, READ_FILE, SEARCH_FILE, WRITE_FILE]
    if wing == "equity_research":
        from departments.equity_research.tools import ERB_FACTS, ERB_LINT, ERB_MEMO, ERB_PEERS
        tools = [ERB_FACTS, ERB_PEERS, *files, GET_MODEL]
        if tier == "lead":
            tools += [ERB_MEMO, ERB_LINT]
        return tools
    if wing == "quant":
        from departments.quant.tools import BUILD_MODEL, DRAFT_ASSUMPTIONS, RUN_SIMULATIONS
        return [*files, DRAFT_ASSUMPTIONS, BUILD_MODEL, RUN_SIMULATIONS, GET_MODEL]
    if wing == "screening":
        from departments.screening.tools import (
            ADD_TO_WATCHLIST,
            PITCH_MEMO,
            READ_SCREEN,
            RUN_SCREEN,
        )
        tools = [READ_SCREEN, PITCH_MEMO, *files, GET_MODEL]
        if tier == "lead":
            tools = [RUN_SCREEN, *tools, ADD_TO_WATCHLIST]
        return tools
    if wing == "client_relations":
        from departments.client_relations.tools import client_tools
        return [*client_tools(tier), LIST_FILES, READ_FILE, SEARCH_FILE, GET_MODEL]
    if wing == "audit":   # Audit reads everything (Quant's drafts included) and writes nothing
        from departments.audit.tools import audit_tools
        return [*audit_tools(tier), LIST_FILES, READ_FILE, SEARCH_FILE, GET_MODEL]
    return [GET_MODEL]
