"""Phase 9: one private office per customer. Two tenants must never see each other's data."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from hq import tenant
from hq.config import CONFIG_DIR
from hq.engine.runtime import Office
from hq.server import create_app
from hq.tools import desk

A, B = "aaaaaaaa1", "bbbbbbbb2"


@pytest.fixture(autouse=True)
def _single_tenant_after():
    yield
    tenant.reset_for_tests()


@pytest.fixture
def host(tmp_path):
    def make(t: tenant.Tenant) -> Office:
        return Office(db_path=t.db, quant_dir=t.quant, memory_dir=t.memory, outbox_dir=t.outbox,
                      tone="rules")

    reg = tenant.Registry(tmp_path / "tenants", office_factory=make)
    reg.create(A)
    reg.create(B)
    app = create_app(registry=reg, resolve_tenant=lambda cookies: cookies.get("t"))
    with TestClient(app, base_url="http://127.0.0.1:8750") as c:
        c.reg = reg
        for who in (A, B):   # offices are built on a customer's first request, inside the server's loop
            as_(c, who).get("/api/state")
        yield c


def as_(c: TestClient, who: str | None) -> TestClient:
    c.cookies.clear()
    if who:
        c.cookies.set("t", who)
    return c


def test_tenant_ids_cannot_be_paths(tmp_path):
    reg = tenant.Registry(tmp_path)
    for bad in ("", "../x", "a/b", "ABCDEFGH", "short", "x" * 40, "..", "a b c d e f g h"):
        with pytest.raises(tenant.TenantError):
            reg.tenant(bad)
    assert reg.create(A).root == tmp_path / A
    with pytest.raises(tenant.TenantError):
        reg.create(A)                       # no taking over an existing tenant
    assert not reg.exists("../..")


def test_nobody_gets_in_without_a_tenant(host):
    assert as_(host, None).get("/api/state").status_code == 401
    assert as_(host, "cccccccc3").get("/api/state").status_code == 401   # unknown id
    assert as_(host, "../../etc").get("/api/state").status_code == 401
    assert as_(host, A).get("/api/state").status_code == 200


def test_chat_and_state_stay_separate(host):
    host.reg.office(A).store.add_chat(channel="captain", sender="captain", recipients=["chief_of_staff"],
                                      text="A's secret plan")
    assert "A's secret plan" in as_(host, A).get("/api/chat").text
    assert "A's secret plan" not in as_(host, B).get("/api/chat").text
    assert "A's secret plan" not in as_(host, B).get("/api/state").text
    assert host.reg.office(A).store is not host.reg.office(B).store
    assert host.reg.tenant(A).db.is_file() and host.reg.tenant(B).db.is_file()


def test_research_files_are_per_tenant(host):
    ta, tb = host.reg.tenant(A), host.reg.tenant(B)
    with tenant.use(ta):
        d = desk.coverage_dir("RMBS")
        d.mkdir(parents=True)
        (d / "brief.md").write_text("A's private brief")
        assert d.is_relative_to(ta.coverage)
    with tenant.use(tb):
        assert desk.coverage_dir("RMBS").is_relative_to(tb.coverage)
        assert not desk.coverage_dir("RMBS").exists()
    assert as_(host, A).get("/files/coverage/RMBS/brief.md").text == "A's private brief"
    assert as_(host, B).get("/files/coverage/RMBS/brief.md").status_code == 404
    assert as_(host, B).get("/files/coverage/RMBS/../../" + A + "/x").status_code in (400, 404)


def test_renaming_a_colleague_changes_only_your_office(host):
    before = CONFIG_DIR.joinpath("roster.yaml").read_text()
    r = as_(host, A).put("/api/members/er_lead", json={"nickname": "Aardvark"})
    assert r.status_code == 200
    assert "Aardvark" in as_(host, A).get("/api/state").text
    assert "Aardvark" not in as_(host, B).get("/api/state").text
    assert CONFIG_DIR.joinpath("roster.yaml").read_text() == before     # the shared template is untouched
    assert "Aardvark" not in host.reg.tenant(B).roster.read_text()


def test_avatars_are_per_tenant(host):
    import base64
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    assert as_(host, A).post("/api/members/er_lead/avatar", json={"data_url": url}).status_code == 200
    assert as_(host, A).get("/avatars/er_lead.png").status_code == 200
    assert as_(host, B).get("/avatars/er_lead.png").status_code == 404


def test_missing_tenant_never_falls_back_to_shared_folders(tmp_path):
    tenant.Registry(tmp_path)
    with pytest.raises(tenant.TenantError):
        desk.coverage_root()
    with pytest.raises(tenant.TenantError):
        desk.coverage_dir("RMBS")


def test_background_work_runs_as_its_tenant(tmp_path):
    reg = tenant.Registry(tmp_path)
    reg.create(A)
    reg.create(B)
    oa, ob = reg.office(A), reg.office(B)

    async def where() -> str:
        await asyncio.sleep(0)
        return tenant.current().id

    async def go():
        # started with no tenant in scope at all, like a timer: the office puts its own back
        return await asyncio.gather(oa.as_tenant(where()), ob.as_tenant(where()))

    assert asyncio.run(go()) == [A, B]


def test_erb_is_pointed_at_the_tenants_own_folder(tmp_path, monkeypatch):
    reg = tenant.Registry(tmp_path)
    t = reg.create(A)
    seen = {}

    async def fake_exec(*a, **kw):
        seen.update(kw["env"])
        raise RuntimeError("stop")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with tenant.use(t), pytest.raises(RuntimeError):
        asyncio.run(desk.run_erb("facts", "RMBS"))
    assert seen["ERB_COVERAGE_DIR"] == str(t.coverage)


def test_screens_live_in_the_tenants_folder(tmp_path):
    reg = tenant.Registry(tmp_path)
    t = reg.create(A)
    with tenant.use(t):
        assert desk.screen_dir("core", "2026-10-09").is_relative_to(t.coverage)
        assert desk.latest_screen_dir("core") is None


def test_an_office_with_shared_folders_is_refused(tmp_path):
    reg = tenant.Registry(tmp_path / "t", office_factory=lambda t: Office(db_path=t.db, tone="rules"))
    reg.create(A)
    with pytest.raises(tenant.TenantError):
        reg.office(A)


def test_the_live_stream_is_per_tenant(host):
    from starlette.websockets import WebSocketDisconnect

    def next_event(ws):
        got = ws.receive_json()
        while got["type"] == "ping":
            got = ws.receive_json()
        return got

    def publish(who, **kw):   # on the server's own loop: the bus is not thread-safe
        host.portal.call(lambda: host.reg.office(who).bus.publish(**kw))

    def subscribed(who):
        return len(host.reg.office(who).bus._subscribers)

    with as_(host, A).websocket_connect("/ws") as wa, as_(host, B).websocket_connect("/ws") as wb:
        for _ in range(200):   # the server subscribes just after the handshake
            if subscribed(A) == 1 and subscribed(B) == 1:
                break
            time.sleep(0.01)
        assert subscribed(A) == 1 and subscribed(B) == 1
        publish(A, type_="chat", agent="captain", channel="captain", recipients=[], text="only for A")
        publish(B, type_="chat", agent="captain", channel="captain", recipients=[], text="only for B")
        assert "only for A" in str(next_event(wa)) and "only for B" in str(next_event(wb))
        publish(B, type_="chat", agent="captain", channel="captain", recipients=[], text="B again")
        assert "only for A" not in str(next_event(wb))   # B's second event proves A's never arrived
    with pytest.raises(WebSocketDisconnect), as_(host, None).websocket_connect("/ws"):   # no cookie, no stream
        pass


def test_a_new_tenant_is_not_the_captain(tmp_path):
    reg = tenant.Registry(tmp_path)
    t = reg.create(A)
    assert "nickname: Captain" in t.roster.read_text() and "Stott" not in t.roster.read_text()
    assert not list(tmp_path.glob(".new-*"))


def test_two_sign_ups_for_one_id_do_not_corrupt_it(tmp_path):
    reg = tenant.Registry(tmp_path)
    (tmp_path / A).mkdir()   # the other sign-up won the race
    with pytest.raises(tenant.TenantError):
        reg.create(A)
    assert not list(tmp_path.glob(".new-*"))


def test_a_host_must_say_who_each_request_is(tmp_path):
    with pytest.raises(ValueError):
        create_app(registry=tenant.Registry(tmp_path))


def test_an_office_on_the_shared_database_is_refused(tmp_path):
    reg = tenant.Registry(tmp_path / "t", office_factory=lambda t: Office(
        db_path=tmp_path / "shared.db", quant_dir=t.quant, memory_dir=t.memory, outbox_dir=t.outbox, tone="rules"))
    reg.create(A)
    with pytest.raises(tenant.TenantError):
        reg.office(A)
