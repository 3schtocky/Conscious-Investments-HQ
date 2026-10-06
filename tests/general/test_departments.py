"""Departments are folders of Markdown, and agents are named by role id, so a rename in Settings
changes what people see and nothing else."""

from __future__ import annotations

import pytest
from conftest import nick, text_turn

from HQ import demo
from HQ.departments import brief


def test_charters_name_colleagues_by_role_and_the_current_nickname_is_filled_in(make_office):
    office, _ = make_office()
    office.agents["screen_associate"].nickname = "Priya"          # the Captain renamed the associate
    prompt = office.agents["screen_lead"].system_prompt()
    assert "Priya" in prompt and "{screen_associate}" not in prompt
    assert nick("screen_associate") not in prompt                  # no stale hard-coded name
    assert "Your department: Screening" in prompt and "what we hunt" in prompt   # charter + playbook


def test_every_agent_prompt_has_no_unfilled_role_placeholder(make_office):
    office, _ = make_office()
    for agent in office.agents.values():
        prompt = agent.system_prompt()
        assert not [i for i in office.agents if "{" + i + "}" in prompt], agent.id


def test_a_department_folder_loads_charter_playbook_and_lessons(tmp_path):
    wing = tmp_path / "w"
    (wing / "playbook").mkdir(parents=True)
    (wing / "charter.md").write_text("# Charter")
    (wing / "playbook" / "02_b.md").write_text("B")
    (wing / "playbook" / "01_a.md").write_text("A")
    (wing / "lessons.md").write_text("- check the margin move is earned")
    (wing / "README.md").write_text("for humans only")
    (wing / "cases").mkdir()
    (wing / "cases" / "one.md").write_text("a past company")
    text = brief("w", tmp_path)
    assert text.index("# Charter") < text.index("A") < text.index("B") < text.index("check the margin")
    assert "humans only" not in text and "past company" not in text


async def test_the_demo_scenes_work_after_a_rename(make_office):
    office, _ = make_office()
    office.agents["er_lead"].nickname = "Brian Washington"
    office.agents["er_associate"].nickname = "Dana"
    names = demo._names(office.agents["er_lead"].system_prompt())
    assert names["er_lead"] == "Brian Washington" and names["er_associate"] == "Dana"
    turn = demo.think("{er_associate} will pull it.", "Over to {er_associate}.",
                      ("send_message", {"to": ["er_associate"], "text": "Hi {er_associate}"}))
    result = turn({"system": office.agents["er_lead"].system_prompt(), "model": "claude-sonnet-5-5"})
    assert result.content[0]["thinking"] == "Dana will pull it."
    assert result.content[2]["input"] == {"to": ["er_associate"], "text": "Hi Dana"}   # ids stay ids
    assert office.resolve("er_lead") == "er_lead" and office.resolve("Brian Washington") == "er_lead"


# renaming is cosmetic -------------------------------------------------------------------------
def test_display_names_can_be_almost_anything(tmp_path, monkeypatch):
    import shutil

    from HQ import config, roster_edit

    shutil.copy(config.CONFIG_DIR / "roster.yaml", tmp_path / "roster.yaml")
    monkeypatch.setattr(roster_edit, "ROSTER", tmp_path / "roster.yaml")
    for fun in ("Brian Washington", "Dr. Money-Bags", "O'Hara", "Zoë", "Q", "The   Oracle", "Mr. 42"):
        entry = roster_edit.update_member("er_lead", {"nickname": fun})
        assert entry["nickname"] == " ".join(fun.split())
    for bad in ("", "x" * 25, "back`tick", "**bold**", "{captain}", "<b>hi</b>", "back\\slash"):
        with pytest.raises(roster_edit.RosterError):
            roster_edit.update_member("er_lead", {"nickname": bad})
    assert roster_edit.update_member("er_lead", {"nickname": "two\nlines"})["nickname"] == "two lines"
    assert roster_edit.update_member("er_lead", {"nickname": "Anything Goes"})["nickname"] == "Anything Goes"


async def test_an_agent_renamed_to_anything_still_works_end_to_end(make_office):
    office, llm = make_office()
    office.agents["er_lead"].nickname = "Dr. O'Hara-Smith"
    llm.script("er_lead", text_turn("On it."))
    office.assign("er_lead", "Look at EOSE.")
    await office.idle()
    assert [t["status"] for t in office.store.tasks()] == ["done"]
    assert office.resolve("dr. o'hara-smith") == "er_lead" and office.resolve("er_lead") == "er_lead"
    assert "You are **Dr. O'Hara-Smith** (`er_lead`)" in llm.calls[0]["params"]["system"]
    assert office.name("er_lead") == "Dr. O'Hara-Smith"
