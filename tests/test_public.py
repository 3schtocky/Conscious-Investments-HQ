"""Public mode: anyone can watch, only the signed-in Captain can act, and a visitor is never
handed anything private."""

from __future__ import annotations

import json

import pytest
from conftest import nick, text_turn, tool_turn
from fastapi.routing import APIRoute
from starlette.websockets import WebSocketDisconnect

from HQ import outbox
from HQ import public as pub

SITE = "https://site.test"
PASSWORD = "correct-horse-battery-staple-42"
SECRETS = ("SECRET-CAPTAIN-WORDS", "SECRET-ORIGINAL", "SECRET-THINKING", "SECRET-CHAT", "SECRET-REPORT",
           "SECRET-NOTE", "SECRET-PAUSE-REASON", "SECRET-DRAFT")


@pytest.fixture
def site(make_office, monkeypatch):
    from fastapi.testclient import TestClient

    from HQ.server import create_app

    monkeypatch.setenv(pub.PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(pub, "hosts", lambda: {"site.test"})
    monkeypatch.setattr("HQ.server.LOGIN_DELAY", 0)
    monkeypatch.setattr("HQ.quotes.latest", lambda tickers: {t: 100.0 if t != "SPY" else 500.0 for t in tickers})
    office, llm = make_office()
    app = create_app(office_factory=lambda: office, public=True)
    captain = TestClient(app, base_url=SITE)          # same running app; only its cookie differs
    with TestClient(app, base_url=SITE) as visitor:
        visitor.office = captain.office = office
        visitor.llm, visitor.app = llm, app
        assert captain.post("/api/login", json={"password": PASSWORD}, headers={"Origin": SITE}).status_code == 200
        yield visitor, captain


def test_refuses_to_go_public_without_a_password_or_a_host(monkeypatch):
    monkeypatch.setattr(pub, "hosts", lambda: {"site.test"})
    monkeypatch.setenv(pub.PASSWORD_ENV, "short")
    with pytest.raises(SystemExit, match="at least 16 characters"):
        pub.check_ready()
    monkeypatch.setenv(pub.PASSWORD_ENV, PASSWORD)
    pub.check_ready()
    monkeypatch.setattr(pub, "hosts", lambda: set())
    with pytest.raises(SystemExit, match="public.hosts"):
        pub.check_ready()


def test_every_route_is_private_unless_listed(site):
    """Deny by default: a visitor gets 401 from every route except the short public list, so an
    endpoint added later is private until someone decides otherwise."""
    visitor, _ = site
    open_to_visitors = {"/api/session", "/api/login", "/api/public/state", "/api/public/watchlist",
                        "/api/public/portfolio", "/api/public/newsletters", "/api/public/replay",
                        "/api/public/health", "/public/newsletters/{issue_id}/{name}", "/avatars/{name}"}
    checked = 0
    for route in visitor.app.routes:
        if not isinstance(route, APIRoute) or route.path in open_to_visitors:
            continue
        path = route.path
        for name in ("member_id", "agent_id", "area", "ticker", "path:path", "path", "watch_id", "approval_id",
                     "finding_id", "write_id", "incident_id"):
            path = path.replace("{" + name + "}", "1")
        for method in route.methods - {"HEAD"}:
            r = visitor.request(method, path, json={}, headers={"Origin": SITE})
            assert r.status_code == 401, (method, route.path, r.status_code)
            checked += 1
    assert checked >= 25
    assert visitor.get("/").status_code in (200, 503)            # the page itself is public


def test_sign_in_locks_after_wrong_passwords_and_the_cookie_is_strict(site):
    visitor, _ = site
    post = lambda pw, **h: visitor.post("/api/login", json={"password": pw}, headers={"Origin": SITE, **h})
    assert visitor.get("/api/session").json() == {"public": True, "demo": False, "role": "visitor"}
    for _ in range(5):
        assert post("wrong").status_code == 401
    assert post(PASSWORD).status_code == 429                      # locked, even with the right one
    for i in range(60):                                           # a crowd of strangers guessing...
        assert post("wrong", **{"cf-connecting-ip": f"198.51.100.{i}"}).status_code == 401
    other = post(PASSWORD, **{"cf-connecting-ip": "203.0.113.9"})  # ...cannot lock the Captain out
    assert other.status_code == 200
    cookie = other.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert visitor.get("/api/session").json()["role"] == "captain"
    assert visitor.get("/api/state").status_code == 200
    assert visitor.post("/api/logout", headers={"Origin": SITE}).json() == {"role": "visitor"}
    visitor.cookies.clear()
    assert visitor.get("/api/state").status_code == 401


def test_the_captain_keeps_the_whole_office_but_only_from_the_site_itself(site):
    visitor, captain = site
    assert captain.get("/api/state").json()["agents"] and captain.get("/api/audit").status_code == 200
    ok = captain.post("/api/agents/er_lead/pause", json={"reason": "check"}, headers={"Origin": SITE})
    assert ok.status_code == 200 and captain.office.agents["er_lead"].paused
    # a signed-in Captain visiting another site: that site cannot use his cookie
    for origin in ("https://evil.example", "http://127.0.0.1:8750", "null"):
        r = captain.post("/api/agents/er_lead/resume", headers={"Origin": origin})
        assert r.status_code == 403, origin
    assert captain.office.agents["er_lead"].paused
    # reaching the server by its local name gives no special rights in public mode
    assert visitor.get("/api/state", headers={"Host": "127.0.0.1:8750"}).status_code == 401
    assert visitor.get("/api/state", headers={"Host": "attacker.example"}).status_code == 403
    r = captain.get("/api/public/state")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"


async def _busy_office(office, llm):
    llm.script("chief_of_staff", tool_turn(("assign_task", {"to": "er_lead", "title": "RMBS memo", "brief": "SECRET-CAPTAIN-WORDS brief"})),
               tool_turn(("report_to_captain", {"text": "SECRET-REPORT"})), text_turn("Routed."))
    llm.script("er_lead", tool_turn(("send_message", {"to": ["er_associate"], "text": "SECRET-CHAT"}),
                                  ("note_to_self", {"note": "SECRET-NOTE about $48"}),
                                  ("delegate", {"to": "er_associate", "job": "SECRET-CHAT job"})),
               text_turn("SECRET-THINKING done.", thinking="SECRET-THINKING"))
    llm.script("er_associate", tool_turn(("submit_result", {"findings": "SECRET-CHAT result", "confidence": "high"})),
               text_turn("SECRET-CHAT reply"))
    office.captain_send("office", "SECRET-CAPTAIN-WORDS please", original="SECRET-ORIGINAL")
    office.pause("screen_associate", by="captain", reason="SECRET-PAUSE-REASON")
    outbox.save_draft(office, agent_id="cr_associate", title="Draft note", body="SECRET-DRAFT body.",
                      x_post="SECRET-DRAFT", linkedin_post="SECRET-DRAFT")
    await office.idle()


async def test_a_visitor_is_never_handed_anything_private(site):
    visitor, captain = site
    office = visitor.office
    q = office.bus.subscribe()                       # what the live stream is fed from
    await _busy_office(office, visitor.llm)
    office.bus.publish("office_status", None, None, status="open", detail="SECRET-CAPTAIN-WORDS")
    raw = []
    while not q.empty():
        raw.append(q.get_nowait().as_dict())
    assert any("SECRET" in json.dumps(e) for e in raw)             # the secrets really were on the bus
    seen = [e for e in (pub.event(office, e) for e in raw) if e is not None]   # the server's own filter
    stream = json.dumps(seen)
    pages = "".join(visitor.get(p).text for p in ("/api/public/state", "/api/public/watchlist",
                                                  "/api/public/portfolio", "/api/public/newsletters",
                                                  "/api/public/replay", "/api/public/health"))
    for secret in SECRETS:
        assert secret not in stream and secret not in pages, secret
    assert any(secret in captain.get("/api/chat").text for secret in SECRETS)   # the Captain still sees it all
    types = {e["type"] for e in seen}
    assert types <= {"status", "move", "meeting", "chat", "delegated", "task_started", "task_done", "office_status"}
    assert {"status", "chat", "task_started", "delegated"} <= types
    assert {e["text"] for e in seen if e["type"] == "chat"} == {"…"}
    titles = {e["title"] for e in seen if e["type"] == "task_started"}
    assert titles == {"An assignment from Stott", "RMBS memo", "A job for Quill", "Answering a colleague"}
    assert all(set(e) <= {"id", "ts", "type", "agent", "task_id", "status"} for e in seen if e["type"] == "status")

    state = visitor.get("/api/public/state").json()
    assert len(state["agents"]) == 11 and state["events"] == [] and state["chat"] == [] and state["incidents"] == []
    assert state["spend"] == {"today": 0, "cap": 0, "by_agent": {}, "by_model": {}}
    pip = next(a for a in state["agents"] if a["id"] == "screen_associate")
    assert pip["paused"] and set(pip) == {"id", "nickname", "wing", "role", "persona", "model", "model_id",
                                         "tier", "avatar", "status", "paused", "task"}


def test_task_labels_never_quote_the_captain(make_office):
    office, _ = make_office()
    label = lambda **kw: pub.task_label(office, **kw)
    assert label(title="buy NVDA now", kind="assignment", assigned_by="captain") == "An assignment from Stott"
    assert label(title="RMBS memo", kind="assignment", assigned_by="chief_of_staff") == "RMBS memo"
    assert label(title="x", kind="delegation", assigned_by="er_lead") == "A job for Quill"
    assert label(title="Message from Stott", kind="message", assigned_by="captain") == "Answering a colleague"
    assert label(title="Review 2 flags on Scout work", kind="assignment", assigned_by="audit_associate") == "An audit review"
    assert pub.event(office, {"type": "thinking", "agent": "er_lead", "text": "x"}) is None
    assert pub.event(office, {"type": "captain_message", "agent": "er_lead", "text": "x"}) is None
    assert pub.event(office, {"type": "chat", "agent": "captain", "recipients": ["er_lead"], "text": "x"}) is None
    assert pub.event(office, {"type": "status", "agent": "er_lead", "status": "paused", "reason": "x", "by": "y"}) == \
        {"id": None, "ts": None, "type": "status", "agent": "er_lead", "task_id": None, "status": "paused"}


def test_visitors_read_only_finished_work(site):
    visitor, captain = site
    office = visitor.office
    office.add_watch(ticker="BFLY", added_by="screen_lead", source="gems 2026-09-30 #1", thesis="Growth is speeding up",
                     pitch="pitch.md", price=80.0, spy=400.0)
    [w] = visitor.get("/api/public/watchlist").json()
    assert (w["ticker"], w["thesis"], w["pitch"], w["added_by"]) == ("BFLY", "Growth is speeding up", None, "")
    assert w["return"] == pytest.approx(0.25) and w["added_by_name"] == nick("screen_lead")

    draft = outbox.save_draft(office, agent_id="cr_associate", title="Weekly note", body="Plain words.",
                              x_post="Out.", linkedin_post="Out.")
    outbox.render_issue(office, draft["id"])
    url = f"/public/newsletters/{draft['id']}/issue.html"
    assert visitor.get("/api/public/newsletters").json() == [] and visitor.get(url).status_code == 404
    outbox.set_status(office.outbox_dir, draft["id"], "awaiting")
    assert visitor.get(url).status_code == 404                     # still on the Captain's desk
    outbox.set_status(office.outbox_dir, draft["id"], "approved")
    [issue] = visitor.get("/api/public/newsletters").json()
    assert issue["title"] == "Weekly note" and set(issue["files"]) == {"issue.html", "body.html", "header.png"}
    assert "Plain words." in visitor.get(url).text and visitor.get(issue["files"]["header.png"]).status_code == 200
    assert visitor.get(f"/public/newsletters/{draft['id']}/draft.md").status_code == 404   # not a public file
    assert visitor.get(f"/public/newsletters/{draft['id']}/meta.json").status_code == 404
    assert visitor.get(f"/outbox/newsletters/{draft['id']}/issue.html").status_code == 401

    office.store.add_model(ticker="RMBS", version=1, path="/tmp/r.xlsx", created_by="quant_lead",
                           summary={"price_targets": {"bear": 60, "base": 130, "bull": 180}, "rating": "Outperform",
                                    "check": {"ok": True}})
    office.store.set_model_status("RMBS", 1, "approved")
    card = office.propose_position("er_lead", "RMBS", 5, "Thesis.", task_id=None)
    view = visitor.get("/api/public/portfolio").json()
    assert view["pending"] == [] and view["open"] == []            # a card on his desk is his business
    assert captain.post(f"/api/approvals/{card}/decide", json={"decision": "approved"}, headers={"Origin": SITE}).status_code == 200
    board = visitor.get("/api/public/portfolio").json()
    [row] = board["open"]
    assert row["ticker"] == "RMBS" and set(row) == set(pub.POSITION_FIELDS) | {"exit_reason"}
    assert row["thesis"] == "Thesis." and row["exit_reason"] is None
    assert set(board) == set(pub.SCORE_FIELDS) | {"open", "closed", "pending"}
    for path in ("/docs", "/redoc", "/openapi.json"):              # no map of the private endpoints
        assert visitor.get(path).status_code == 404 and captain.get(path).status_code == 404


def test_the_live_stream_in_public_mode(site):
    visitor, captain = site
    with pytest.raises(WebSocketDisconnect), visitor.websocket_connect("/ws", headers={"Origin": "https://evil.example"}) as ws:
        ws.receive_json()
    publish = lambda *a, **kw: visitor.portal.call(lambda: visitor.office.bus.publish(*a, **kw))
    signed_in = {"Origin": SITE, "Cookie": f"{pub.COOKIE}={captain.cookies[pub.COOKIE]}"}
    with visitor.websocket_connect("/ws", headers=signed_in) as ws:
        publish("thinking", "er_lead", None, text="full stream")
        assert ws.receive_json()["text"] == "full stream"
    with visitor.websocket_connect("/ws", headers={"Origin": SITE}) as ws:   # the filter is wired in
        publish("thinking", "er_lead", None, text="SECRET-THINKING")
        publish("chat", "er_lead", None, channel="dm:a|b", recipients=["er_associate"], text="SECRET-CHAT")
        ev = ws.receive_json()
        assert ev["type"] == "chat" and ev["text"] == "…" and "SECRET" not in json.dumps(ev)


def test_local_mode_is_unchanged(make_office):
    from fastapi.testclient import TestClient

    from HQ.server import create_app

    office, _ = make_office()
    with TestClient(create_app(office_factory=lambda: office)) as c:
        assert c.get("/api/session").json() == {"public": False, "demo": False, "role": "captain"}
        assert c.post("/api/login", json={"password": "x"}).status_code == 404
        assert c.get("/api/state").status_code == 200 and "content-security-policy" not in c.get("/api/state").headers
        assert c.get("/api/state", headers={"Host": "consciousinvestments.org"}).status_code == 403


def test_one_visitor_cannot_take_every_seat(site, monkeypatch):
    visitor, _ = site
    monkeypatch.setattr(pub, "MAX_SOCKETS_PER_ADDRESS", 2)
    mine = {"Origin": SITE, "cf-connecting-ip": "203.0.113.5"}
    with visitor.websocket_connect("/ws", headers=mine), visitor.websocket_connect("/ws", headers=mine):
        with pytest.raises(WebSocketDisconnect), visitor.websocket_connect("/ws", headers=mine) as third:
            third.receive_json()
        with visitor.websocket_connect("/ws", headers={"Origin": SITE, "cf-connecting-ip": "203.0.113.6"}) as other:
            visitor.portal.call(lambda: visitor.office.bus.publish("status", "er_lead", None, status="working"))
            assert other.receive_json()["type"] == "status"      # another visitor still gets a seat
    with visitor.websocket_connect("/ws", headers=mine) as again:   # seats are freed when visitors leave
        visitor.portal.call(lambda: visitor.office.bus.publish("status", "er_lead", None, status="idle"))
        assert again.receive_json()["status"] == "idle"


def test_quotes_share_one_download_and_remember_misses(monkeypatch):
    from HQ import quotes

    calls = []
    monkeypatch.setattr(quotes, "_cache", {})
    monkeypatch.setattr(quotes, "_download", lambda tickers: calls.append(list(tickers)) or
                        {t: (None if t == "GONE" else 10.0) for t in tickers})
    assert quotes.latest(["RMBS", "GONE"]) == {"RMBS": 10.0, "GONE": None}
    assert quotes.latest(["RMBS", "GONE"]) == {"RMBS": 10.0, "GONE": None}
    assert calls == [["RMBS", "GONE"]]                             # the miss was not asked for again
    assert quotes.latest(["RMBS", "SPY"])["SPY"] == 10.0 and calls[-1] == ["SPY"]


def test_captain_password_is_written_privately_and_replaces_the_old_one(tmp_path, monkeypatch, capsys):
    from HQ import cli

    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=keep-me\nexport HQ_CAPTAIN_PASSWORD=old\n  HQ_CAPTAIN_PASSWORD=older\n")
    monkeypatch.setattr("HQ.config.ROOT", tmp_path)
    monkeypatch.delenv(pub.PASSWORD_ENV, raising=False)
    assert cli.captain_password() == 0
    lines = env.read_text().splitlines()
    assert lines[0] == "ANTHROPIC_API_KEY=keep-me" and len(lines) == 2
    value = lines[1].split("=", 1)[1]
    assert lines[1].startswith("HQ_CAPTAIN_PASSWORD=") and len(value) >= 32 and value in capsys.readouterr().out
    assert env.stat().st_mode & 0o777 == 0o600 and [p.name for p in tmp_path.iterdir()] == [".env"]


async def test_the_replay_is_the_live_stream_replayed_and_leaks_nothing(site):
    visitor, _ = site
    office = visitor.office
    assert visitor.get("/api/public/replay").json() == {"events": [], "from": None, "to": None}
    await _busy_office(office, visitor.llm)
    office.bus.publish("office_status", None, None, status="open", detail="SECRET-CAPTAIN-WORDS")
    body = visitor.get("/api/public/replay")
    for secret in SECRETS:
        assert secret not in body.text, secret
    data = body.json()
    events = data["events"]
    assert events and data["from"] == events[0]["ts"] and data["to"] == events[-1]["ts"]
    assert [e["ts"] for e in events] == sorted(e["ts"] for e in events)
    assert {e["type"] for e in events} <= {"status", "move", "meeting", "chat", "delegated", "task_started", "task_done"}
    assert {e["text"] for e in events if e["type"] == "chat"} == {"…"}
    assert {"An assignment from Stott", "RMBS memo"} <= {e["title"] for e in events if e["type"] == "task_started"}
    assert all(e["task_id"] is None and e["agent"] in office.agents for e in events)


def test_replay_takes_only_the_latest_stretch_of_work(make_office):
    office, _ = make_office()
    now = 1_000_000_000.0
    for ts, agent in ((now - 3 * 24 * 3600, "er_lead"), (now - 3 * 24 * 3600 + 60, "er_lead"),
                      (now - 600, "quant_lead"), (now - 300, "quant_lead")):
        eid = office.store.add_event("status", agent, None, {"status": "working"})
        office.store._exec("UPDATE events SET ts=? WHERE id=?", (ts, eid))
    old = office.store.add_event("status", "er_lead", None, {"status": "idle"})
    office.store._exec("UPDATE events SET ts=? WHERE id=?", (now - 9 * 24 * 3600, old))
    got = pub.replay(office, now=now)["events"]
    assert [e["agent"] for e in got] == ["quant_lead", "quant_lead"]   # an older burst and a stale one are left out


def test_replay_is_capped(make_office):
    office, _ = make_office()
    now = 1_000_000_000.0
    for i in range(300):
        eid = office.store.add_event("move", "er_lead", None, {"to": "desk:quant_lead"})
        office.store._exec("UPDATE events SET ts=? WHERE id=?", (now - 300 + i, eid))
    got = pub.replay(office, now=now)["events"]
    assert len(got) == pub.REPLAY_MAX and got[-1]["ts"] == now - 1


def test_health_says_only_whether_the_office_answers(site):
    visitor, captain = site
    assert visitor.get("/api/public/health").json() == {"ok": True, "paused": False, "clocked_out": False}
    captain.office.hold()
    assert visitor.get("/api/public/health").json()["paused"] is True
