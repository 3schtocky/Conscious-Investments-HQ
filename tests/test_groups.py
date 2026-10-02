"""Group chats (Stott -> all, Juno -> all), post limits, and agent document history."""

from __future__ import annotations

from conftest import register_model, text_turn, tool_turn


async def test_stott_announcement_reaches_everyone_and_replies_stay_in_the_thread(make_office):
    from hq.demo import DemoLLM

    office, _ = make_office()
    office._llm = DemoLLM(speed=1000, office=office)
    out = office.captain_send("all", "We're adding a Quant review to every memo from Monday.")
    await office.idle()
    assert out["routed_to"] == "all" and len(out["task_ids"]) == 11
    thread = office.store.chat("group:stott")
    assert thread[0]["sender"] == "captain"
    repliers = {m["sender"] for m in thread[1:]}
    assert len(repliers) == 11 and "captain" not in repliers
    # replies never fan out: exactly one task per agent
    assert len(office.store.tasks()) == 11


async def test_juno_announces_in_her_group_and_everyone_else_replies(make_office):
    from hq.demo import DemoLLM

    office, _ = make_office()
    llm = DemoLLM(speed=1000, office=office)
    office._llm = llm
    llm.script("chief_of_staff",
               lambda p: tool_turn(("post_to_group", {"group": "juno",
                                                      "text": "Lobby sync at 3pm today."})),
               lambda p: text_turn("Announced."))
    office.assign("Juno", "Tell everyone about the 3pm sync.")
    await office.idle()
    thread = office.store.chat("group:juno")
    assert thread[0]["sender"] == "chief_of_staff" and thread[0]["text"] == "Lobby sync at 3pm today."
    assert {m["sender"] for m in thread[1:]} == set(office.agents) - {"chief_of_staff"}


async def test_only_juno_broadcasts_and_posts_are_limited_per_task(make_office):
    office, llm = make_office()
    llm.script("Quill",
               tool_turn(("post_to_group", {"group": "juno", "text": "first"}),
                         ("post_to_group", {"group": "stott", "text": "second note"}),
                         ("post_to_group", {"group": "stott", "text": "a third, different one"}),
                         ("post_to_group", {"group": "nowhere", "text": "x"})),
               text_turn("done"))
    office.assign("Quill", "x")
    await office.idle()
    results = [c for c in llm.calls if c["who"] == "Quill"][1]["params"]["messages"][-1]["content"]
    assert [r.get("is_error", False) for r in results] == [False, False, True, True]
    assert "already posted 2 times" in results[2]["content"]
    assert len(office.store.tasks()) == 1   # Quill's post in Juno's group didn't fan out


async def test_document_history(make_office):
    office, llm = make_office()
    register_model(office, "RMBS", 1)
    llm.script("Sigma",
               tool_turn(("delegate", {"to": "Delta", "job": "Build the sensitivity grid."})),
               tool_turn(("request_approval", {"kind": "model", "ticker": "RMBS", "version": 1,
                                               "title": "RMBS model v1", "summary": "Base case.",
                                               "attachments": ["RMBS_v1.xlsx"]})),
               tool_turn(("report_to_captain", {"text": "RMBS v1 is on your desk."})),
               text_turn("Done."))
    llm.script("Delta", tool_turn(("submit_result", {"findings": "Grid built, 5x5.",
                                                     "confidence": "high"})))
    office.assign("Sigma", "RMBS v1")
    await office.idle()
    sigma = office.documents("Sigma")
    assert [d["source"] for d in sigma][:2] == ["report", "approval"]
    assert "model" in [d["source"] for d in sigma]   # the registered workbook version
    assert sigma[1]["attachments"] == ["RMBS_v1.xlsx"] and sigma[1]["version"] == 1
    delta = office.documents("quant_associate")
    assert delta[0]["source"] == "delegation" and delta[0]["text"] == "Grid built, 5x5."


def test_documents_and_all_endpoints(make_office):
    from fastapi.testclient import TestClient

    from hq.server import create_app

    office, _ = make_office()
    office.tone_engine = "rules"
    with TestClient(create_app(office_factory=lambda: office)) as c:
        assert c.get("/api/agents/Sigma/documents").json() == []
        assert c.get("/api/agents/nobody/documents").status_code == 404
        prev = c.post("/api/captain/preview", json={"to": "all", "text": "Big week ahead"}).json()
        assert prev["rewrite"].startswith("Hi team,")


async def test_rejected_group_posts_dont_use_up_the_allowance(make_office):
    office, llm = make_office()
    llm.script("Quill",
               tool_turn(("post_to_group", {"group": "nowhere", "text": "x"}),
                         ("post_to_group", {"group": "stott", "text": "first real post"}),
                         ("post_to_group", {"group": "stott", "text": "first real post!"}),
                         ("post_to_group", {"group": "stott", "text": "a second, different post"})),
               text_turn("done"))
    office.assign("Quill", "x")
    await office.idle()
    results = [c for c in llm.calls if c["who"] == "Quill"][1]["params"]["messages"][-1]["content"]
    assert [r.get("is_error", False) for r in results] == [True, False, True, False]


async def test_demo_answers_every_announcement(make_office):
    from hq.demo import DemoLLM

    office, _ = make_office()
    office._llm = DemoLLM(speed=1000, office=office)
    for text in ("First announcement.", "Second announcement."):
        office.captain_send("all", text)
        await office.idle()
    replies = [m for m in office.store.chat("group:stott") if m["sender"] != "captain"]
    assert len(replies) == 22


def test_documents_tolerate_odd_saved_results(make_office):
    office, _ = make_office()
    tid = office.store.create_task(assignee="er_associate", assigned_by="er_lead",
                                   kind="delegation", title="odd", body="x")
    office.store.set_task_status(tid, "done", result="[1, 2]")
    [doc] = office.documents("Ledger")
    assert doc["text"] == "[1, 2]"
