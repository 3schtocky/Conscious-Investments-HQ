"""The final audit: figures tie out by code, failures go back as targeted redos, two redos max."""

from __future__ import annotations

import pytest
from conftest import register_model

from hq import finalaudit
from hq.tools import desk


@pytest.fixture
def coverage(tmp_path, monkeypatch):
    root = tmp_path / "erb"
    (root / "coverage" / "RMBS" / "facts").mkdir(parents=True)
    monkeypatch.setattr(desk, "ERB_DIR", root)
    d = root / "coverage" / "RMBS"
    (d / "facts" / "financials_annual.csv").write_text("year,revenue\n2025,458200000\n")
    return d


def _setup(office, coverage, memo):
    register_model(office)
    office.store.set_model_status("RMBS", 1, "approved")
    (coverage / "memo.md").write_text(memo)


def test_clean_memo_ties_out_for_free(make_office, coverage):
    office, _ = make_office()
    _setup(office, coverage, "# Thesis\n\nRevenue was $458.2 million in 2025. Our price target is $2.00.\n")
    r = finalaudit.audit_documents(office, "RMBS")
    assert r["exceptions"] == [] and r["matched"] == r["figures"] == 2


@pytest.mark.asyncio
async def test_unsourced_figure_becomes_a_flag_per_sentence(make_office, coverage):
    office, _ = make_office()
    _setup(office, coverage, "# Thesis\n\nGross margin reached 61.4% and revenue was $458.2 million.\n")
    r = finalaudit.run(office, "RMBS")
    assert len(r["findings"]) == 1
    f = office.store.finding(r["findings"][0])
    assert f["rule"] == "unsourced_figure" and f["agent"] == "er_lead" and "61.4%" in f["detail"]
    assert "$458.2 million" not in f["detail"].split("for:")[1]


@pytest.mark.asyncio
async def test_redo_is_targeted_counted_and_capped(make_office, coverage):
    office, _ = make_office()
    _setup(office, coverage, "# Thesis\n\nGross margin reached 61.4%.\n")
    fid = finalaudit.run(office, "RMBS")["findings"][0]
    with pytest.raises(ValueError, match="upheld"):
        finalaudit.request_redo(office, [fid], by="audit_lead")
    for round_ in range(finalaudit.MAX_REDOS):
        office.store.set_finding(fid, "upheld", by="audit_lead")
        r = finalaudit.request_redo(office, [fid], by="audit_lead")
        assert len(r["tasks"]) == 1 and not r["escalated"]
        task = office.store.task(r["tasks"][0])
        assert task["assignee"] == "er_lead" and "only these" in task["body"] and "61.4%" in task["body"]
    office.store.set_finding(fid, "upheld", by="audit_lead")
    r = finalaudit.request_redo(office, [fid], by="audit_lead")
    assert r["tasks"] == [] and r["escalated"] == [fid]
    assert any("Still failing" in i["detail"] for i in office.store.incidents())


@pytest.mark.asyncio
async def test_after_redo_closes_what_now_ties_out(make_office, coverage):
    office, _ = make_office()
    _setup(office, coverage, "# Thesis\n\nGross margin reached 61.4%.\n")
    fid = finalaudit.run(office, "RMBS")["findings"][0]
    office.store.set_finding(fid, "upheld", by="audit_lead")
    task = office.store.task(finalaudit.request_redo(office, [fid], by="audit_lead")["tasks"][0])
    assert finalaudit.after_redo(office, task) == []          # unchanged: still failing
    (coverage / "memo.md").write_text("# Thesis\n\nWe could not source the margin and removed it.\n")
    assert finalaudit.after_redo(office, task) == [fid]
    assert office.store.finding(fid)["status"] == "cleared"


def test_offer_card_once_per_version(make_office, coverage):
    office, _ = make_office()
    _setup(office, coverage, "# Thesis\n\nFine.\n")
    assert finalaudit.maybe_offer(office, "RMBS") is not None
    assert finalaudit.maybe_offer(office, "RMBS") is None


@pytest.mark.asyncio
async def test_view_start_and_offer_after_a_cr_approval(make_office, coverage):
    office, _ = make_office()
    _setup(office, coverage, "# Thesis\n\nGross margin reached 61.4%.\n")
    with pytest.raises(ValueError, match="not a ticker"):
        finalaudit.start(office, "../x")
    r = finalaudit.start(office, "rmbs")
    assert r["exceptions"] == 1 and r["figures"] == 1
    v = finalaudit.view(office)
    [t] = v["tickers"]
    assert t["ticker"] == "RMBS" and t["last_run"]["exceptions"] == 1 and t["open"] == 1 and t["held"] == 0
    assert office.audit_view()["final"]["tickers"][0]["ticker"] == "RMBS"
    card = {"kind": "deliverable", "payload": {"ticker": "RMBS"}}
    assert len(finalaudit.offer_for_card(office, card)) == 1
    assert finalaudit.offer_for_card(office, {"kind": "memo", "payload": {}}) == []
