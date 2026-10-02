"""The office web server: REST for state and Settings, one WebSocket for live events.

    uv run hq serve            # the real office (API key needed only once work is assigned)
    uv run hq serve --demo     # scripted demo office, zero API cost, separate database

Binds to 127.0.0.1 only: the office is for the Captain's own machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hq import roster_edit
from hq.config import DATA_DIR, ROOT
from hq.engine.runtime import Office

log = logging.getLogger(__name__)
WEB_DIST = ROOT / "web" / "dist"


class MemberEdit(BaseModel):
    nickname: str | None = None
    persona: str | None = None
    model: str | None = None
    avatar: str | dict | None = None


class AvatarUpload(BaseModel):
    data_url: str


class PauseRequest(BaseModel):
    reason: str = "Paused by the Captain"


class CaptainPreview(BaseModel):
    to: str = "office"
    text: str


class CaptainSend(BaseModel):
    to: str = "office"
    text: str                     # what the team will read
    original: str | None = None   # the Captain's own words, if he sent the rewrite


class CaptainCheck(BaseModel):
    original: str
    rewrite: str


class Decision(BaseModel):
    decision: str                 # approved | changes | rejected
    note: str | None = None


class MemoryDecision(BaseModel):
    decision: str                 # approved | rejected

MAX_MESSAGE = 8000

# The office has no login: it trusts whoever can reach it, which is why it only listens on this
# machine. A browser on this machine can still be steered by another website, so every request
# must be addressed to a local name (blocks DNS rebinding) and every change, and the live event
# stream, must come from the office's own page (blocks cross-site requests and socket hijacking).
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
EXTRA_HOSTS: set[str] = set()   # tests add their client's host name here


def _hostname(value: str | None) -> str:
    """The host part of a Host header or an Origin URL, without scheme or port."""
    host = (value or "").strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    if host.startswith("["):                  # [::1]:8750
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _local(value: str | None) -> bool:
    return _hostname(value) in LOCAL_HOSTS | EXTRA_HOSTS


def request_allowed(method: str, headers) -> bool:
    """Whether a request may be served: local Host always; for anything that changes state, or
    the event stream, also a local Origin (browsers attach Origin to every cross-site write)."""
    if not _local(headers.get("host")):
        return False
    if method in ("GET", "HEAD", "OPTIONS"):
        return True
    origin = headers.get("origin")
    if origin is not None:
        return origin != "null" and _local(origin)
    return headers.get("sec-fetch-site") in (None, "same-origin", "none")   # non-browser clients


def create_app(*, demo: bool = False, demo_speed: float = 1.0,
               office_factory=None) -> FastAPI:
    state: dict = {}

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        if demo:
            from hq.demo import DemoLLM, run_demo

            db = DATA_DIR / "demo.db"
            for suffix in ("", "-wal", "-shm"):
                Path(f"{db}{suffix}").unlink(missing_ok=True)
            import shutil
            shutil.rmtree(DATA_DIR / "demo-outbox", ignore_errors=True)
            llm = DemoLLM(speed=demo_speed)
            office = Office(db_path=db, llm=llm, tone="rules", quant_dir=DATA_DIR / "demo-quant",
                            memory_dir=DATA_DIR / "demo-memory", outbox_dir=DATA_DIR / "demo-outbox")
            llm.office = office

            async def loop() -> None:
                while True:
                    try:
                        await run_demo(office, llm)
                    except Exception:   # keep the demo alive while tuning the UI
                        log.exception("demo loop crashed; restarting")
                        await asyncio.sleep(3)

            state["demo_task"] = asyncio.create_task(loop())
        else:
            office = office_factory() if office_factory else Office()
        state["office"] = office

        async def reminders() -> None:   # plain code, no API call
            while True:
                try:
                    office.weekly_reminder()      # Juno's Monday nudge
                except Exception:
                    log.exception("weekly reminder failed")
                try:
                    if not demo:
                        office.daily_digest()     # Tally's digest for each finished day
                except Exception:
                    log.exception("daily digest failed")
                try:
                    if office.store.positions():   # mark to market, raise exit flags
                        from hq import portfolio
                        office.portfolio_tick(await portfolio.fetch_prices(office))
                except Exception:
                    log.exception("portfolio tick failed")
                await asyncio.sleep(1800)

        state["reminder_task"] = asyncio.create_task(reminders())
        yield
        state["reminder_task"].cancel()
        task = state.get("demo_task")
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Conscious Investments HQ", lifespan=lifespan)

    @app.middleware("http")
    async def same_machine_only(request, call_next):
        if not request_allowed(request.method, request.headers):
            return JSONResponse({"detail": "The office only answers its own page on this machine."},
                                status_code=403)
        return await call_next(request)

    def office() -> Office:
        return state["office"]

    @app.get("/api/state")
    async def get_state() -> JSONResponse:
        snap = office().snapshot()
        snap["office"]["demo"] = demo
        return JSONResponse(snap)

    @app.get("/api/events")
    async def get_events(task_id: int | None = None, limit: int = 500) -> JSONResponse:
        rows = office().store.events(task_id=task_id, limit=min(limit, 2000))
        return JSONResponse([{"id": e["id"], "ts": e["ts"], "type": e["type"],
                              "agent": e["agent"], "task_id": e["task_id"], **e["payload"]}
                             for e in rows])

    @app.get("/api/chat")
    async def get_chat(channel: str | None = None, limit: int = 200) -> JSONResponse:
        return JSONResponse(office().store.chat(channel, limit=min(limit, 1000)))

    @app.put("/api/members/{member_id}")
    async def edit_member(member_id: str, edit: MemberEdit) -> JSONResponse:
        changes = edit.model_dump(exclude_none=True)
        try:
            entry = roster_edit.update_member(member_id, changes)
        except roster_edit.RosterError as e:
            raise HTTPException(400, str(e)) from e
        office().reload_roster()
        office().bus.publish("roster_updated", member_id if member_id != "captain" else None,
                             None, member=member_id, entry=entry)
        return JSONResponse(entry)

    @app.post("/api/members/{member_id}/avatar")
    async def upload_avatar(member_id: str, upload: AvatarUpload) -> JSONResponse:
        if member_id != "captain" and member_id not in office().agents:
            raise HTTPException(404, "No such member.")
        try:
            path = roster_edit.save_avatar_image(member_id, upload.data_url)
            entry = roster_edit.update_member(member_id, {"avatar": {"image": path}})
        except roster_edit.RosterError as e:
            raise HTTPException(400, str(e)) from e
        office().reload_roster()
        office().bus.publish("roster_updated", None, None, member=member_id, entry=entry)
        return JSONResponse(entry)

    @app.get("/files/{area}/{ticker}/{path:path}")
    async def files(area: str, ticker: str, path: str) -> FileResponse:
        """Download a model or research file, confined to one ticker's folder."""
        from hq.tools.desk import TICKER, coverage_dir

        if not TICKER.match(ticker) or area not in ("quant", "coverage"):
            raise HTTPException(404)
        root = (office().quant_dir if area == "quant" else coverage_dir(ticker).parent) / ticker
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target, filename=target.name)

    @app.get("/api/watchlist")
    async def watchlist() -> JSONResponse:
        from hq import quotes

        rows = office().store.watchlist()
        tickers = sorted({w["ticker"] for w in rows} | {"SPY"})
        prices = await asyncio.to_thread(quotes.latest, tickers) if rows else {}
        return JSONResponse(office().watchlist_view(prices))

    @app.post("/api/watchlist/{watch_id}/research")
    async def watch_research(watch_id: int) -> JSONResponse:
        try:
            return JSONResponse(office().send_watch_to_research(watch_id))
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e

    @app.post("/api/watchlist/{watch_id}/drop")
    async def watch_drop(watch_id: int) -> JSONResponse:
        office().store.set_watch_status(watch_id, "dropped")
        office().bus.publish("watchlist_status", None, None, watch=watch_id, status="dropped")
        return JSONResponse({"ok": True})

    @app.get("/api/outbox")
    async def outbox_view() -> JSONResponse:
        return JSONResponse(office().outbox_view())

    @app.get("/outbox/{path:path}")
    async def outbox_file(path: str) -> FileResponse:
        """A newsletter or deliverable file, confined to the Outbox folder."""
        root = office().outbox_dir.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file() or target.name.startswith("."):
            raise HTTPException(404)
        return FileResponse(target)

    @app.get("/api/models")
    async def models(ticker: str | None = None) -> JSONResponse:
        return JSONResponse(office().store.models(ticker))

    @app.get("/avatars/{name}")
    async def avatar(name: str) -> FileResponse:
        path = roster_edit.avatar_file(name)
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/agents/{agent_id}/pause")
    async def pause(agent_id: str, req: PauseRequest) -> JSONResponse:
        try:
            office().pause(agent_id, by="captain", reason=req.reason)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        return JSONResponse({"ok": True})

    @app.post("/api/agents/{agent_id}/resume")
    async def resume(agent_id: str) -> JSONResponse:
        try:
            office().resume(agent_id, by="captain")
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        return JSONResponse({"ok": True})

    def _check_text(text: str) -> str:
        text = text.strip()
        if not text:
            raise HTTPException(400, "Write a message first.")
        if len(text) > MAX_MESSAGE:
            raise HTTPException(400, f"Messages are limited to {MAX_MESSAGE} characters.")
        return text

    def _check_to(to: str) -> str:
        if to in ("office", "all"):
            return to
        try:
            return office().resolve(to)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e

    @app.post("/api/captain/preview")
    async def captain_preview(req: CaptainPreview) -> JSONResponse:
        return JSONResponse(await office().tone_preview(_check_to(req.to), _check_text(req.text)))

    @app.post("/api/captain/check")
    async def captain_check(req: CaptainCheck) -> JSONResponse:
        from hq.engine.tone import check

        return JSONResponse(check(req.original[:MAX_MESSAGE], req.rewrite[:MAX_MESSAGE]).as_dict())

    @app.post("/api/captain/send")
    async def captain_send(req: CaptainSend) -> JSONResponse:
        original = req.original.strip()[:MAX_MESSAGE] if req.original else None
        return JSONResponse(office().captain_send(_check_to(req.to), _check_text(req.text),
                                                  original=original))

    @app.get("/api/agents/{agent_id}/documents")
    async def documents(agent_id: str) -> JSONResponse:
        try:
            return JSONResponse(office().documents(agent_id))
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e

    @app.get("/api/approvals")
    async def approvals(status: str | None = None) -> JSONResponse:
        return JSONResponse(office().store.approvals(status=status))

    async def _portfolio_prices(extra: list[str] | None = None) -> dict:
        from hq import portfolio

        return await portfolio.fetch_prices(office(), extra)

    @app.get("/api/portfolio")
    async def portfolio_view() -> JSONResponse:
        return JSONResponse(office().portfolio_view(await _portfolio_prices()))

    @app.post("/api/approvals/{approval_id}/decide")
    async def decide(approval_id: int, req: Decision) -> JSONResponse:
        try:
            prices = None
            pending = office().store.approval(approval_id)
            if pending["kind"] == "portfolio" and req.decision == "approved":
                prices = await _portfolio_prices([pending["payload"].get("ticker", "")])   # off the event loop
            card = office().decide(approval_id, req.decision, (req.note or "").strip() or None,
                                   prices=prices)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return JSONResponse(card)

    @app.get("/api/audit")
    async def audit_view() -> JSONResponse:
        return JSONResponse(office().audit_view())

    @app.get("/api/audit/digest")
    async def audit_digest(day: str | None = None) -> JSONResponse:
        from datetime import date

        if day is not None:
            try:
                date.fromisoformat(day)
            except ValueError as e:
                raise HTTPException(400, "day must look like 2026-10-01") from e
        return JSONResponse(office().digest(day))

    @app.post("/api/audit/findings/{finding_id}/review")
    async def finding_review(finding_id: int) -> JSONResponse:
        try:
            return JSONResponse({"task_id": office().review_finding(finding_id)})
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/audit/findings/{finding_id}/dismiss")
    async def finding_dismiss(finding_id: int) -> JSONResponse:
        try:
            return JSONResponse(office().resolve_finding(finding_id, "dismissed", by="captain"))
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/memory/{write_id}/decide")
    async def memory_decide(write_id: int, req: MemoryDecision) -> JSONResponse:
        try:
            return JSONResponse(office().decide_memory(write_id, req.decision))
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/memory/{write_id}/remove")
    async def memory_remove(write_id: int) -> JSONResponse:
        try:
            return JSONResponse(office().remove_memory(write_id))
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/incidents/{incident_id}/resolve")
    async def resolve_incident(incident_id: int) -> JSONResponse:
        office().resolve_incident(incident_id)
        return JSONResponse({"ok": True})

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        if not request_allowed("WEBSOCKET", socket.headers):   # another site must not listen in
            await socket.close(code=1008)
            return
        await socket.accept()
        q = office().bus.subscribe()
        try:
            while True:
                event = await q.get()
                await socket.send_json(event.as_dict())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            office().bus.unsubscribe(q)

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/")
        async def no_ui() -> JSONResponse:
            return JSONResponse({"error": "The office UI isn't built yet. Run: cd web && npm install "
                                          "&& npm run build"}, status_code=503)

    return app


def serve(*, demo: bool, port: int, speed: float) -> None:
    import uvicorn

    uvicorn.run(create_app(demo=demo, demo_speed=speed), host="127.0.0.1", port=port,
                log_level="warning")
