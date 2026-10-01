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

MAX_MESSAGE = 8000


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
            llm = DemoLLM(speed=demo_speed)
            office = Office(db_path=db, llm=llm, tone="rules", quant_dir=DATA_DIR / "demo-quant",
                            memory_dir=DATA_DIR / "demo-memory")
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

        async def reminders() -> None:   # Juno's Monday nudge (plain code, no API call)
            while True:
                try:
                    office.weekly_reminder()
                except Exception:
                    log.exception("weekly reminder failed")
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

    @app.post("/api/approvals/{approval_id}/decide")
    async def decide(approval_id: int, req: Decision) -> JSONResponse:
        try:
            card = office().decide(approval_id, req.decision, (req.note or "").strip() or None)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return JSONResponse(card)

    @app.post("/api/incidents/{incident_id}/resolve")
    async def resolve_incident(incident_id: int) -> JSONResponse:
        office().resolve_incident(incident_id)
        return JSONResponse({"ok": True})

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
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
