"""The demo office answers whatever the Captain types, from real office data, with no filler."""

from __future__ import annotations

import re

import pytest
from conftest import nick

from hq import demo
from hq.demo import DemoLLM


@pytest.fixture
def demo_office(make_office):
    office, _ = make_office()
    office._llm = DemoLLM(speed=1000, office=office)
    return office


def _to_captain(office, who=None):
    return [m for m in office.store.chat() if "captain" in m["recipients"]
            and (who is None or m["sender"] == who)]


def test_tickers_are_picked_out_of_a_sentence():
    assert demo._tickers("Please start research on EOSE and compare it with FLNC, ASAP.") == ["EOSE", "FLNC"]
    assert demo._tickers("Use the DCF and tell the CEO") == []
    assert demo._tickers("Look at RMBS, AMD, NVDA and META today") == ["RMBS", "AMD", "NVDA"]


async def test_whats_going_on_is_answered_from_the_real_board(demo_office):
    office = demo_office
    office.request_approval("quant_lead", kind="other", title="Model v1 for review", summary="s",
                            payload={}, task_id=None)
    office.captain_send("office", "Hey Juno, what's going on around the office?")
    await office.idle()
    [reply] = _to_captain(office, "chief_of_staff")
    assert "Model v1 for review" in reply["text"]
    assert "decision" in reply["text"] and "(demo" not in reply["text"]
    assert not [t for t in office.store.tasks() if t["assigned_by"] == "chief_of_staff"]   # no work invented


async def test_a_message_to_everyone_goes_to_every_lead(demo_office):
    office = demo_office
    office.captain_send("office", "Team, only work on EOSE until I say otherwise.")
    await office.idle()
    got = {t["assignee"] for t in office.store.tasks() if t["assigned_by"] == "chief_of_staff"}
    assert got == {"er_lead", "screen_lead", "quant_lead", "audit_lead", "cr_lead"}
    [reply] = _to_captain(office, "chief_of_staff")
    assert nick("er_lead") in reply["text"] and nick("quant_lead") in reply["text"]


async def test_a_standing_instruction_is_acknowledged_not_researched(demo_office):
    office = demo_office
    office.captain_send("office", "Team, only work on EOSE for now.")
    await office.idle()
    assert not [t for t in office.store.tasks() if t["kind"] == "delegation"]   # nobody went digging
    assert all(t["status"] == "done" for t in office.store.tasks())
    assert not [m for m in _to_captain(office) if m["sender"] != "chief_of_staff"]   # leads don't pile on


async def test_the_followup_names_the_task_not_the_wrapper(demo_office):
    office = demo_office
    office.captain_send("office", "Please research Pinecrest Robotics for me.")
    await office.idle()
    text = _to_captain(office, "er_lead")[-1]["text"]
    assert 'First pass on "Please research Pinecrest Robotics for me."' in text
    assert "New assignment" not in text and "**" not in text


async def test_a_ticker_in_the_request_reaches_the_associate(demo_office):
    office = demo_office
    office.captain_send("office", "Start initiating coverage on EOSE and FLNC.")
    await office.idle()
    job = next(t for t in office.store.tasks() if t["kind"] == "delegation")
    assert "EOSE, FLNC" in job["body"]


async def test_a_lead_reports_its_own_status_honestly(demo_office):
    office = demo_office
    first = office.assign("er_lead", "Write up RMBS.")
    await office.idle()
    office.request_approval("er_lead", kind="brief", title="RMBS brief", summary="s", payload={},
                            task_id=first)
    office.captain_send("er_lead", "How is the coverage report coming along?")
    await office.idle()
    text = _to_captain(office, "er_lead")[-1]["text"]
    assert 'latest assignment was "Write up RMBS."' in text and "finished" in text
    assert 'waiting on "RMBS brief"' in text


async def test_a_lead_with_nothing_on_says_so(demo_office):
    office = demo_office
    office.captain_send("screen_lead", "What are you working on?")
    await office.idle()
    assert "Nothing is on my desk" in _to_captain(office, "screen_lead")[-1]["text"]


async def test_thanks_gets_a_short_answer_the_captain_sees(demo_office):
    office = demo_office
    office.captain_send("er_lead", "Thanks, great work on that.")
    await office.idle()
    text = _to_captain(office, "er_lead")[-1]["text"]
    assert text.startswith("Thank you, Stott")


async def test_consecutive_announcements_get_different_replies_per_role(demo_office):
    office = demo_office
    for said in ("Memos need a Quant review.", "Newsletters go out on Thursdays."):
        office.captain_send("all", said)
        await office.idle()
    replies = [m for m in office.store.chat("group:stott") if m["sender"] == "quant_lead"]
    assert len(replies) == 2 and replies[0]["text"] != replies[1]["text"]
    assert replies[0]["text"].startswith('On "Memos need a Quant review"')
    assert all("(demo" not in m["text"] for m in office.store.chat("group:stott"))


async def test_spoken_scene_lines_carry_no_demo_tag(demo_office):
    office = demo_office
    for scene in (demo.scene_memo, demo.scene_lobby_sync, demo.scene_audit):
        office.conversations = demo.ConversationGuard()
        scene(office, office._llm)
        await office.idle()
    # (a review brief legitimately quotes a card title, which keeps its tag)
    spoken = [m["text"] for m in office.store.chat()
              if m["sender"] != "captain" and not m["text"].startswith("New assignment:")]
    assert spoken and not [t for t in spoken if re.search(r"\(demo", t)]


async def test_the_memo_scene_rotates_its_company(demo_office):
    office = demo_office
    seen = []
    for _ in range(3):
        office.conversations = demo.ConversationGuard()
        demo.scene_memo(office, office._llm)
        await office.idle()
        seen.append(office.store.approvals("pending")[-1]["payload"]["ticker"])
    assert len(set(seen)) == 3
