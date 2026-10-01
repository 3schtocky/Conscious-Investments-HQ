"""Phase 2: roster editing, the web server and the demo scenes."""

from __future__ import annotations

import asyncio
import base64
import shutil

import pytest

from hq import roster_edit


@pytest.fixture
def roster_copy(tmp_path, monkeypatch):
    path = tmp_path / "roster.yaml"
    shutil.copy(roster_edit.ROSTER, path)
    monkeypatch.setattr(roster_edit, "ROSTER", path)
    monkeypatch.setattr(roster_edit, "AVATAR_DIR", tmp_path / "avatars")
    from hq import config
    shutil.copy(config.CONFIG_DIR / "mission.md", tmp_path / "mission.md")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return path


# roster editing ---------------------------------------------------------------------------
def test_edit_keeps_comments_and_applies_changes(roster_copy):
    entry = roster_edit.update_member("er_lead", {
        "nickname": "Quincy", "persona": "Calm and exacting senior analyst who loves footnotes.",
        "model": "associate",
        "avatar": {"skin": "#8d5a3b", "hairStyle": "curly", "accessory": "glasses"}})
    assert entry["nickname"] == "Quincy" and entry["model"] == "associate"
    text = roster_copy.read_text()
    assert "# The cast." in text and "nickname: Quincy" in text
    assert "hairStyle: curly" in text
    # untouched agents unchanged
    assert "nickname: Ledger" in text


def test_edit_validation(roster_copy):
    with pytest.raises(roster_edit.RosterError, match="already taken"):
        roster_edit.update_member("er_lead", {"nickname": "ledger"})
    with pytest.raises(roster_edit.RosterError, match="already taken"):
        roster_edit.update_member("er_lead", {"nickname": "Captain"})
    with pytest.raises(roster_edit.RosterError, match="1 to 24"):
        roster_edit.update_member("er_lead", {"nickname": "x" * 30})
    with pytest.raises(roster_edit.RosterError, match="hex colour"):
        roster_edit.update_member("er_lead", {"avatar": {"skin": "red"}})
    with pytest.raises(roster_edit.RosterError, match="one of"):
        roster_edit.update_member("er_lead", {"avatar": {"hairStyle": "mohawk"}})
    with pytest.raises(roster_edit.RosterError, match="Model must be"):
        roster_edit.update_member("er_lead", {"model": "claude-opus-5-5"})
    with pytest.raises(roster_edit.RosterError, match="Can't edit"):
        roster_edit.update_member("er_lead", {"wing": "audit"})
    with pytest.raises(roster_edit.RosterError, match="Can't edit"):
        roster_edit.update_member("captain", {"persona": "The boss, obviously and always."})


def test_avatar_upload_png_only(roster_copy):
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
    path = roster_edit.save_avatar_image("er_lead", "data:image/png;base64,"
                                         + base64.b64encode(png).decode())
    assert path.startswith("/avatars/er_lead.png?v=")
    assert roster_edit.avatar_file("er_lead.png").read_bytes() == png
    with pytest.raises(roster_edit.RosterError, match="PNG"):
        roster_edit.save_avatar_image("er_lead", "data:image/png;base64,"
                                      + base64.b64encode(b"GIF89a").decode())
    with pytest.raises(roster_edit.RosterError):
        roster_edit.save_avatar_image("../etc", "data:image/png;base64,AAAA")
    assert roster_edit.avatar_file("../office.db") is None


# server -----------------------------------------------------------------------------------
@pytest.fixture
def client(make_office, roster_copy):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, llm = make_office()
    app = create_app(office_factory=lambda: office)
    with TestClient(app) as c:
        c.office, c.llm = office, llm
        yield c


def test_state_endpoint(client):
    snap = client.get("/api/state").json()
    assert len(snap["agents"]) == 11 and snap["office"]["demo"] is False
    assert snap["spend"]["cap"] == 10.0 and snap["captain"]["nickname"]
    assert {a["tier"] for a in snap["agents"]} == {"lead", "associate"}


def test_member_edit_endpoint_reloads_roster(client):
    r = client.put("/api/members/er_lead", json={"nickname": "Quincy"})
    assert r.status_code == 200
    assert client.office.agents["er_lead"].nickname == "Quincy"
    bad = client.put("/api/members/er_lead", json={"nickname": "Ledger"})
    assert bad.status_code == 400 and "taken" in bad.json()["detail"]


def test_pause_resume_endpoints(client):
    assert client.post("/api/agents/Quill/pause", json={"reason": "check"}).status_code == 200
    assert client.office.agents["er_lead"].paused
    assert client.post("/api/agents/Quill/resume").status_code == 200
    assert not client.office.agents["er_lead"].paused


def test_websocket_streams_events(client):
    with client.websocket_connect("/ws") as ws:
        client.office.bus.publish("status", "er_lead", None, status="working")
        ev = ws.receive_json()
        assert ev["type"] == "status" and ev["agent"] == "er_lead" and ev["status"] == "working"


# demo scenes ------------------------------------------------------------------------------
async def test_every_demo_scene_runs_clean(make_office, monkeypatch):
    from hq.demo import SCENES, DemoLLM

    monkeypatch.setattr("hq.tools.desk.latest_screen_dir", lambda preset: None)   # no network in tests

    office, _ = make_office()
    llm = DemoLLM(speed=1000)
    office._llm = llm
    for scene in SCENES:
        scene(office, llm)
        await office.idle()
    tasks = office.store.tasks()
    assert tasks and all(t["status"] == "done" for t in tasks), \
        [(t["id"], t["assignee"], t["status"], t["status_reason"]) for t in tasks]
    types = {e["type"] for e in office.store.events(limit=5000)}
    assert {"meeting", "delegated", "captain_report", "move", "chat"} <= types
    assert "guard_block" not in types and "task_error" not in types
    assert not any(llm.scripts.values())   # every scripted turn was used


async def test_demo_loop_resets_guard_between_cycles(make_office, monkeypatch):
    from hq.demo import SCENES, DemoLLM, run_demo

    monkeypatch.setattr("hq.tools.desk.latest_screen_dir", lambda preset: None)   # no network in tests

    office, _ = make_office()
    llm = DemoLLM(speed=1000)
    office._llm = llm
    task = asyncio.create_task(run_demo(office, llm, pause=0))
    while len(office.store.tasks()) < 3 * len(SCENES) * 2:   # at least ~two full loops
        await asyncio.sleep(0.05)
    task.cancel()
    types = [e["type"] for e in office.store.events(limit=10000)]
    assert "guard_block" not in types


# Regression tests for the Phase 2 code review ---------------------------------------------
def test_captain_may_keep_the_name_captain(roster_copy):
    assert roster_edit.update_member("captain", {"nickname": "Captain"})["nickname"] == "Captain"


def test_unchanged_explicit_model_id_does_not_block_a_save(roster_copy):
    text = roster_copy.read_text().replace("    model: lead\n", "    model: claude-opus-5-5\n", 1)
    roster_copy.write_text(text)
    agent = next(a for a in __import__("yaml").safe_load(text)["agents"]
                 if a["model"] == "claude-opus-5-5")
    entry = roster_edit.update_member(agent["id"], {"nickname": "Renamed",
                                                    "model": "claude-opus-5-5"})
    assert entry["model"] == "claude-opus-5-5" and entry["nickname"] == "Renamed"


def test_pause_unknown_agent_is_404(client):
    assert client.post("/api/agents/nobody/pause", json={}).status_code == 404
    assert client.post("/api/agents/nobody/resume").status_code == 404


async def test_model_change_mid_task_waits_for_the_next_task(make_office):
    from conftest import text_turn, tool_turn

    office, llm = make_office()
    started = asyncio.Event()

    async def first(params):
        started.set()
        await asyncio.sleep(0.02)
        return tool_turn(("send_message", {"to": ["Ledger"], "text": "hi"}))

    llm.script("Quill", first, text_turn("done"))
    llm.script("Ledger", text_turn("ok"))
    office.assign("Quill", "x")
    await started.wait()
    quill = office.agents["er_lead"]
    quill.model_cfg = {**quill.model_cfg, "id": "switched-model"}   # a Settings edit mid-task
    await office.idle()
    models = {c["params"]["model"] for c in llm.calls if c["who"] == "Quill"}
    assert models == {"fake"}


async def test_demo_keeps_its_pretend_budget_in_range(make_office, monkeypatch):
    monkeypatch.setattr("hq.tools.desk.latest_screen_dir", lambda preset: None)
    from hq.demo import DemoLLM, run_demo

    office, _ = make_office(daily_cap=1.0)   # a few loops of pretend spend per "day"
    llm = DemoLLM(speed=1000)
    office._llm = llm
    task = asyncio.create_task(run_demo(office, llm, pause=0))
    while len(office.store.tasks()) < 72:   # six loops, so the reset must kick in
        await asyncio.sleep(0.02)
    task.cancel()
    statuses = {t["status"] for t in office.store.tasks()}
    assert "paused_budget" not in statuses


# Quant Department ---------------------------------------------------------------------------
def test_quant_wing_in_roster_and_prompts(make_office):
    office, _ = make_office()
    sigma, delta = office.agents["quant_lead"], office.agents["quant_associate"]
    assert (sigma.wing, sigma.tier, delta.wing, delta.tier) == ("quant", "lead", "quant", "associate")
    assert office.wing_name("quant") == "Quant"
    prompt = sigma.system_prompt()
    assert "# Your department: Quant" in prompt and "An honest model beats a flattering one" in prompt
    assert "Excel model (.xlsx)" in delta.system_prompt()
    # other wings don't get the Quant charter; everyone sees Sigma and Delta as colleagues
    quill = office.agents["er_lead"].system_prompt()
    assert "Your department: Quant" not in quill and "Sigma (`quant_lead`)" in quill
    # Sigma can delegate to Delta (same wing) but not to Ledger
    from hq.tools.office import tools_for
    assert "delegate" in [t.name for t in tools_for(sigma.tier, sigma.id)]


def test_agents_call_the_captain_by_his_settings_name(make_office):
    office, _ = make_office()
    assert office.captain_name == "Stott"
    prompt = office.agents["quant_lead"].system_prompt()
    assert "{captain}" not in prompt and "escalate to Stott with the numbers" in prompt
    assert "Stott (Ethan Stott) is the Captain of the fund" in prompt
    from hq.tools.office import definitions, tools_for
    defs = definitions(tools_for("lead", "er_lead"), captain=office.captain_name)
    report = next(d for d in defs if d["name"] == "report_to_captain")
    assert report["description"].startswith("Send a message to Stott, the Captain")
    office.captain_name = "Boss"   # a Settings rename flows into new tasks' prompts
    assert "Boss (Ethan Stott) is the Captain" in office.agents["er_lead"].system_prompt()


async def test_demo_keeps_only_a_few_pending_cards(make_office, monkeypatch):
    monkeypatch.setattr("hq.tools.desk.latest_screen_dir", lambda preset: None)
    from hq.demo import DEMO_PENDING_KEEP, DemoLLM, run_demo

    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    task = asyncio.create_task(run_demo(office, llm, pause=0))
    while len(office.store.approvals()) < 8:
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)
    task.cancel()
    assert len(office.store.approvals("pending")) <= DEMO_PENDING_KEEP + 3   # + one loop's worth
    assert office.store.approvals("expired")
