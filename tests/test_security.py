"""The office has no login, so it must only ever answer its own page on this machine."""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from hq.server import _hostname, request_allowed


@pytest.fixture
def client(make_office):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, _ = make_office()
    with TestClient(create_app(office_factory=lambda: office), base_url="http://127.0.0.1:8750") as c:
        c.office = office
        yield c


def test_host_and_origin_parsing():
    assert _hostname("127.0.0.1:8750") == "127.0.0.1" and _hostname("localhost") == "localhost"
    assert _hostname("[::1]:8750") == "::1" and _hostname("http://localhost:5173") == "localhost"
    assert _hostname("https://evil.example/path") == "evil.example" and _hostname(None) == ""
    ok = lambda method, **h: request_allowed(method, h)
    assert ok("GET", host="127.0.0.1:8750") and ok("POST", host="localhost:8750", origin="http://localhost:5173")
    assert ok("POST", host="127.0.0.1:8750")                                    # curl, the CLI
    assert not ok("GET", host="office.attacker.example:8750")                   # DNS rebinding
    assert not ok("POST", host="127.0.0.1:8750", origin="https://evil.example")
    assert not ok("POST", host="127.0.0.1:8750", origin="null")                 # sandboxed frame
    assert not ok("POST", host="127.0.0.1:8750", **{"sec-fetch-site": "cross-site"})
    assert not ok("WEBSOCKET", host="127.0.0.1:8750", origin="https://evil.example")
    assert not ok("POST", host="127.0.0.1.evil.example")


def test_another_website_cannot_drive_or_read_the_office(client):
    evil = {"Origin": "https://evil.example"}
    office = client.office
    # its own page works
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/agents/Quill/pause", json={"reason": "check"},
                       headers={"Origin": "http://127.0.0.1:8750"}).status_code == 200
    assert office.agents["er_lead"].paused
    # a cross-site page cannot unpause, send work (which would spend credits) or decide a card
    assert client.post("/api/agents/Quill/resume", headers=evil).status_code == 403
    assert office.agents["er_lead"].paused
    assert client.post("/api/captain/send", json={"to": "office", "text": "Research NVDA"}, headers=evil).status_code == 403
    assert office.store.tasks() == []
    watch = office.add_watch(ticker="BFLY", added_by="screen_lead", source="gems", thesis="x", pitch=None,
                             price=1.0, spy=1.0)
    assert client.post(f"/api/watchlist/{watch}/research", headers=evil).status_code == 403
    assert client.post(f"/api/watchlist/{watch}/drop", headers=evil).status_code == 403
    assert office.store.watch(watch)["status"] == "watching"
    # a rebinding attack reaches the server under the attacker's own host name
    assert client.get("/api/state", headers={"Host": "attacker.example:8750"}).status_code == 403
    assert client.get("/api/chat", headers={"Host": "attacker.example:8750"}).status_code == 403


def test_the_live_stream_is_closed_to_other_origins(client):
    with client.websocket_connect("/ws", headers={"Origin": "http://127.0.0.1:8750"}) as ws:
        client.portal.call(lambda: client.office.bus.publish("status", "er_lead", None, status="working"))
        assert ws.receive_json()["type"] == "status"
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws", headers={"Origin": "https://evil.example"}) as ws:
        ws.receive_json()
