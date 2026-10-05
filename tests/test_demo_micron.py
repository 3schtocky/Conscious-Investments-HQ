"""The Micron demo: the recording runs on the real tools, the script is well-formed, every number the
visitor sees is the approved model's, and nothing about it loosens what the public site shows live.

These tests need the Micron files in the Equity Research repo (gitignored), so they skip on a fresh clone.
"""

from __future__ import annotations

import json
import re

import pytest

from hq import demo_export, demo_micron, demo_script, portfolio, quotes
from hq import public as pub
from hq.config import ERB_DIR

COVERAGE = ERB_DIR / "coverage" / "MU"
pytestmark = pytest.mark.skipif(not (COVERAGE / "model.json").exists(), reason="no Micron coverage in this checkout")


@pytest.fixture(scope="module")
def run():
    events, office = demo_micron.record(speed=40.0)
    names = {a.id: a.nickname for a in office.agents.values()}
    return events, office, names, demo_script.build(events, names)


@pytest.fixture(scope="module")
def model():
    return json.loads((COVERAGE / "model.json").read_text())


def test_the_recording_ran_every_act_on_the_real_tools(run):
    _, office, _, _ = run
    assert all(t["status"] == "done" for t in office.store.tasks())
    assert {(a["kind"], a["status"]) for a in office.store.approvals()} == {
        ("model", "approved"), ("newsletter", "approved"), ("portfolio", "approved")}
    [pos] = office.store.positions("open")
    assert (pos["ticker"], pos["size_pct"], round(pos["entry_value"], 2), round(pos["entry_price"], 2)) == (
        "MU", 10, 10_000.0, 1062.29)


def test_ten_percent_is_a_demo_only_size_and_the_real_rules_are_untouched(run):
    assert portfolio.SIZES == (3, 5, 8)
    assert quotes.latest is not demo_micron.record   # the recorder put the real quote function back
    assert quotes.latest.__name__ == "latest"


def test_the_script_is_five_and_a_half_minutes_in_seven_acts(run):
    _, _, _, tour = run
    beats = tour["beats"]
    assert tour["duration"] == 340.0
    assert [b["t"] for b in beats] == sorted(b["t"] for b in beats)
    assert 0 <= beats[0]["t"] and beats[-1]["t"] < tour["end_card"]
    assert [a["label"] for a in tour["acts"]][5] == "Do we own it?"
    assert len(tour["acts"]) == 7 and [a["t"] for a in tour["acts"]] == sorted(a["t"] for a in tour["acts"])
    assert [u["artifact"] for u in tour["unlocks"]] == ["report", "model", "note", "position"]
    assert tour["unlocks"][-1]["t"] > tour["acts"][5]["t"]   # the position opens inside the buy act


def test_the_script_carries_only_allowed_fields_and_no_nickname(run):
    _, _, names, tour = run
    for b in tour["beats"]:
        assert set(b) <= set(demo_script.FIELDS[b["type"]]) | {"t", "type", "agent"}, b
        for text in (v for v in b.values() if isinstance(v, str)):
            found = [n for n in names.values() if re.search(rf"\b{re.escape(n)}\b", text)]
            assert not found, (found, text)
    stott = [b for b in tour["beats"] if b["type"] == "captain_message"]
    assert len(stott) == 5 and "{chief_of_staff}" in stott[0]["text"] and "ten percent" in stott[2]["text"]


def test_every_number_the_visitor_sees_is_the_approved_models(run, model):
    _, _, _, tour = run
    base, bear, bull = (model["scenarios"][k]["price_target"] for k in ("base", "bear", "bull"))
    assert model["rating"]["rating"] == "Outperform"
    cover = (COVERAGE / "sections" / "00_cover.md").read_text()
    for target in (base, bear, bull):
        assert f"${target:,.2f}" in cover
    final = [b for b in tour["beats"] if b["type"] == "captain_report"][-1]["text"]
    assert f"${base:,.2f}" in final and "Outperform" in final and "10% of the paper portfolio" in final
    assert not demo_export.TAG.search(demo_export.markdown("A figure [S1] and [M].", model))


def test_the_exported_report_has_no_scaffold_leftovers(model):
    for section in demo_export.report({**model, "name": "Micron Technology, Inc."})["sections"]:
        assert "{{" not in section["html"] and "-->" not in section["html"] and "VERIFY" not in section["html"], section["id"]


def test_a_visitor_watching_live_still_never_sees_words(run):
    events, office, _, _ = run
    spoken = next(e for e in events if e["type"] == "chat")
    shown = pub.event(office, spoken)
    assert shown is not None and shown["text"] == "…"
    assert pub.event(office, next(e for e in events if e["type"] == "captain_message")) is None
