"""Export the public views to plain JSON so the visitor site can live on Cloudflare Pages.

The static site cannot run the office. It plays back a replay of the most recent real work, using the
same sanitized views the live public site serves (HQ.public), so a guest sees movement and who works,
never words. Run `uv run hq export-static`, then build the page with `npm run build:static` in web/.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from HQ import public as pub
from HQ.config import DATA_DIR, ROOT
from HQ.engine.runtime import Office

SITE_DATA = ROOT / "GUI" / "web" / "public" / "data"


def _snapshot(db: Path, into: Path) -> Path:
    """A consistent copy of the database (WAL included), so the live office is never touched."""
    dest = into / "office.db"
    src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close(), dst.close()
    return dest


def _prices(office: Office, offline: bool) -> tuple[dict, dict]:
    if offline:
        return {}, {}
    try:
        from departments.executive import portfolio
        from HQ import quotes

        rows = office.store.watchlist()
        tickers = sorted({w["ticker"] for w in rows} | {"SPY"})
        watch = quotes.latest(tickers) if rows else {}
        import asyncio
        return watch, asyncio.run(portfolio.fetch_prices(office))
    except Exception as e:  # noqa: BLE001 - a price outage must not block publishing the replay
        print(f"prices unavailable ({e}); watchlist and portfolio shown without live returns")
        return {}, {}


SHOWCASE_DB = DATA_DIR / "showcase.db"


def build_showcase(speed: float = 8.0, rounds: int = 1) -> Path:
    """Run every demo scene through the real engine into a fresh database, so the guest replay
    shows the whole office (screening, research, quant, audit, portfolio, newsletter) at work."""
    import asyncio

    from HQ.demo import DemoLLM, run_demo

    for suffix in ("", "-wal", "-shm"):
        Path(f"{SHOWCASE_DB}{suffix}").unlink(missing_ok=True)
    shutil.rmtree(DATA_DIR / "showcase-outbox", ignore_errors=True)

    async def go() -> None:
        llm = DemoLLM(speed=speed)
        office = Office(db_path=SHOWCASE_DB, llm=llm, tone="rules", quant_dir=DATA_DIR / "demo-quant",
                        memory_dir=DATA_DIR / "showcase-memory", outbox_dir=DATA_DIR / "showcase-outbox")
        llm.office = office
        await run_demo(office, llm, pause=0.5, rounds=rounds)
        await office.idle()
        # The showcase plays the Captain once: approve the newsletter issue and the portfolio entry
        # (real prices, placeholder demo model) so a guest sees those tabs filled. Demo data only.
        from departments.executive import portfolio

        for card in office.store.approvals("pending"):
            if card["kind"] in ("newsletter", "portfolio"):
                prices = await portfolio.fetch_prices(office, [card["payload"].get("ticker", "")]) \
                    if card["kind"] == "portfolio" else None
                try:
                    office.decide(card["id"], "approved", None, prices=prices)
                except (KeyError, ValueError) as e:
                    print(f"showcase: could not approve {card['kind']} card: {e}")

    asyncio.run(go())
    print(f"Showcase recorded -> {SHOWCASE_DB}")
    return SHOWCASE_DB


def export(db: Path | None = None, out: Path | None = None, *, outbox: Path | None = None,
           offline: bool = False, max_events: int = pub.REPLAY_MAX) -> dict:
    db = db or DATA_DIR / "office.db"
    out = out or SITE_DATA
    if not db.is_file():
        raise SystemExit(f"No database at {db}. Run the office once, or pass --db.")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        office = Office(db_path=_snapshot(db, Path(tmp)), tone="rules", outbox_dir=outbox or ROOT / "outbox")
        state = pub.state(office, demo=db.name.startswith(("demo", "showcase")))   # scripted source -> DEMO badge
        state["office"].update(held=False, clocked_out=False)
        for a in state["agents"]:
            a.update(status="idle", paused=False, task=None)   # a replay starts from a quiet floor
        latest = max((r["ts"] for r in office.store.events_where(types=pub.REPLAY_TYPES, limit=600)), default=None)
        replay = pub.replay(office, now=(latest or 0) + 1, max_events=max_events)
        watch_prices, port_prices = _prices(office, offline)
        files = {
            "state": state,
            "replay": replay,
            "watchlist": pub.watchlist(office, watch_prices),
            "portfolio": pub.portfolio(office, port_prices),
            "newsletters": pub.newsletters(office),
        }
        for name, body in files.items():
            (out / f"{name}.json").write_text(json.dumps(body, separators=(",", ":")))
        for issue in files["newsletters"]:
            for fname in issue["files"]:
                src = pub.newsletter_file(office, issue["id"], fname)
                if src:
                    (out / "newsletters" / issue["id"]).mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, out / "newsletters" / issue["id"] / fname)
    summary = {"replay_events": len(replay["events"]), "from": replay["from"], "to": replay["to"],
               "newsletters": len(files["newsletters"]), "watchlist": len(files["watchlist"]), "out": str(out)}
    print(f"Exported {summary['replay_events']} replay events, {summary['newsletters']} issues -> {out}")
    return summary
