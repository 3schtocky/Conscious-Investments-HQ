"""The five-minute Micron demo: one real initiation, start to finish, led by the Chief of Staff.

`record()` runs the scene once through the real engine on a throwaway database (the model workbook,
the simulations, the report lint, the audit lookups and the newsletter gate are all REAL tools on the
real Micron files in the Equity Research repo; only the agents' words are scripted) and captures every
event. `hq.demo_script` then cuts that recording down to a timed, word-carrying script that each
visitor's browser plays back from 0:00. Nothing here calls the Anthropic API.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

from hq.config import DATA_DIR
from hq.demo import DemoLLM, _delta_reports, _last_tool_json, think
from hq.engine.runtime import Office

T = "MU"
RECORD_DB = DATA_DIR / "micron-demo.db"
RECORD_DIR = DATA_DIR / "micron-demo"

OPENING = ("Juno, we should initiate coverage on Micron (MU). I want the full report, a bull, base and "
           "bear model for it, and Quill and Sigma working together on it.")
APPROVE_NOTE = ("Approved. Have Vera check the report against that model, and ask Harbor for a short "
                "note to clients on it.")
WRAP_REQUEST = "Thank you all. Juno, pull the leads together for a one-line wrap."


# ---- the newsletter (400 to 600 words; every number is the approved model's or a filing's) ------
def newsletter(material: dict) -> dict:
    name = next(n for n in material["researched_names"] if n["ticker"] == T)
    pt, cite = name["price_targets"], name["cite_as"]
    base, bear, bull = pt["base"], pt["bear"], pt["bull"]
    body = f"""## This week: Micron (MU), initiated

The research desk has finished its initiation of Micron Technology (MU), one of the three companies that make most of the world's memory chips. The business changed shape in a year. Revenue was $133.2 bn in fiscal 2026 against $37.4 bn in fiscal 2025, and the gross margin in the fourth quarter was 86.8% against 44.7% a year earlier, according to the company's 30 September earnings release.

## Why the numbers moved

Management says demand for memory from AI data centers is growing faster than the industry can add supply, and that prices and margins have risen across its products. Since the spring the company has also been signing multi-year agreements with customers who commit to buy fixed volumes at fixed or banded prices, and customers have put $12.7 bn on deposit under them. The company also holds about $68.3 bn more cash than debt. That is a stronger position than memory makers have usually had.

## What we think

Our rating on Micron (MU) is Underperform. {cite} puts the base-case price target for MU at ${base:,.2f}, with a bear case of ${bear:,.2f} and a bull case of ${bull:,.2f}, against a share price of $1,062.29 when we built the model. The rating compares the base case with what we expect the S&P 500 to return over 15 months. It is not a view that the business is weak. It is a view about price: the shares have risen 272.4% since the start of the year, and they trade at 5.9x the earnings analysts expect for the next twelve months, so the market is already treating these profits as temporary. Our base case agrees, and fades the margin from 83.0% in fiscal 2027 to 65.0% in fiscal 2031 as new factories in Idaho, Singapore and Taiwan add supply from the middle of calendar 2027.

## How wide the range is

The distance between the bear and bull cases is the point. In the bear case, prices reset as new capacity arrives and revenue falls to $110.0 bn in fiscal 2028. In the bull case, supply stays short and the customer agreements hold the gross margin near 86% for years. Memory has always moved in cycles: in fiscal 2023 the company's revenue fell 49.5% and its gross margin turned negative. The new agreements may soften the next downturn. They do not remove it, and their price floors have not been published in full.

## What would change our mind

Three things. A disclosed floor price that implies a gross margin above 75.0% through fiscal 2028. Two quarters in a row of revenue above $65.0 bn. Or a lower share price, because our rating is relative to the price you pay and a lower price changes it.

## How this note was made

Every figure comes from a filing or from the approved model, and the office checked the report against the model before this note was written. Our price targets are produced by the model, not by the people writing about it. This is research, not personal advice, and it is a demonstration of how the office works.
"""
    x = (f"Our Micron (MU) initiation: Underperform, base-case target ${base:,.2f}, bear ${bear:,.2f}, bull "
         f"${bull:,.2f} ({cite.split(',')[0]}). A demonstration of how the office works, not investment advice.")
    linkedin = (f"We have initiated coverage of Micron (MU). We rate MU Underperform, with a base-case price "
                f"target of ${base:,.2f} for MU, a bear case of ${bear:,.2f} and a bull case of ${bull:,.2f}. "
                "Revenue grew from $37.4 bn to $133.2 bn in fiscal 2026 and the stock has already risen 272.4% "
                "this year, so the question is how long the margin lasts. The note explains our range and what "
                "would change our mind. This is research, not personal advice, and a demonstration of how "
                "the office works.")
    return {"title": "Micron (MU): the margin question (demo)", "body": body, "x_post": x,
            "linkedin_post": linkedin}


# ---- scripted agents ---------------------------------------------------------------------------
def _model_card(params: dict) -> object:
    """Sigma files the model for Stott's approval, with the real numbers from the tool result."""
    text = _last_tool_json(params).get("findings", "")
    m = re.search(r"model v(\d+)", text)
    version = int(m.group(1)) if m else 1
    return think("The build checks out against the engine. Filing it for Stott's approval with the "
                 "three cases and the simulation range attached.", None,
                 ("request_approval", {"kind": "model", "ticker": T, "version": version,
                                       "title": f"Micron (MU) model v{version}: bear, base and bull",
                                       "summary": text or f"{T} model v{version} is ready.",
                                       "attachments": [f"quant/{T}_model_v{version}.xlsx"]}))(params)


def _juno_wrap(params: dict) -> object:
    m = _last_tool_json(params)
    pt, ret = m.get("price_targets", {}), m.get("total_returns", {})
    text = (f"Micron is done. The report, the model and the client note are in. Rating {m.get('rating')}: base "
            f"${pt.get('base', 0):,.2f}, bear ${pt.get('bear', 0):,.2f}, bull ${pt.get('bull', 0):,.2f} "
            f"({m.get('cite_as')}). Audit tied the report to the model, and the note passed the "
            "publishing checks.")
    return think("All three pieces are done and the numbers match. One report to Stott.", None,
                 ("report_to_captain", {"text": text}))(params)


def script(office: Office, llm: DemoLLM) -> None:
    """Everything the agents say. The tools they call are real."""
    # Juno: three jobs in the story (kick off, hand on after approval, wrap up)
    llm.script("chief_of_staff",
               think("Micron is a full initiation: Research writes the report and Quant builds the model, "
                     "so both leads start now and talk to each other.", None,
                     ("assign_task", {"to": "er_lead", "title": "Initiate coverage: Micron (MU)",
                                      "brief": "Write the full initiating-coverage report on Micron (MU) from the "
                                               "facts pack and filings in coverage/MU. Sources on every figure; "
                                               "no price target of your own, Quant owns the model."}),
                     ("assign_task", {"to": "quant_lead", "title": "Micron (MU) model: bear, base, bull",
                                      "brief": "Build the Micron (MU) model from coverage/MU/assumptions.yaml: "
                                               "formula check, simulations, a bear, base and bull case. File it "
                                               "for Stott's approval."})),
               think("Both desks have it.", "Research and Quant are on Micron."),
               think("Stott approved the model. Audit checks the report against it and Client Relations "
                     "writes the note, in parallel.", None,
                     ("assign_task", {"to": "audit_lead", "title": "Check the Micron report against the model",
                                      "brief": "Check that every price target, rating and return in the Micron "
                                               "report matches the approved model. Report any mismatch."}),
                     ("assign_task", {"to": "cr_lead", "title": "Micron client note",
                                      "brief": "Write a short note to clients on the Micron initiation, using only "
                                               "the approved model's numbers. File it for Stott."})),
               think("Both are under way.", "Audit and Client Relations have it."),
               think("A one-line wrap from each lead, then one report for Stott.", None,
                     ("send_message", {"to": ["er_lead", "quant_lead", "cr_lead"],
                                       "text": "Wrap on Micron: one line each on what you would tell a client."})),
               think("Reading the approved model so the wrap carries the exact numbers.", None,
                     ("get_model", {"ticker": T})),
               _juno_wrap,
               think("Done.", "Micron wrapped for Stott."))

    # Research: the associate reads the filings, the lead checks the report with the real lint
    llm.script("er_lead",
               think("The facts pack and the filings are in coverage/MU. {er_associate} pulls the key "
                     "facts so I can check every figure in the report against a source.", None,
                     ("delegate", {"to": "er_associate", "job": "Read facts/facts.md and the 8-K earnings "
                                   "release for Micron. Return the five numbers the thesis rests on, "
                                   "each with its source."})),
               think("The facts agree with the draft. Now the style and sourcing lint on all eleven "
                     "sections.", None, ("erb_lint", {"ticker": T})),
               think("Lint is clean. The report goes to Stott with the model.", None,
                     ("report_to_captain", {"text": "The Micron initiation is written: 16 pages, every "
                                            "figure tagged to a filing or the model, style and sourcing "
                                            "checks clean. The price targets come from Sigma's model."})),
               think("Done.", "Micron report written and checked."))
    llm.script("er_associate",
               think("Start with what we have for Micron.", None, ("list_files", {"ticker": T})),
               think("The facts pack first.", None,
                     ("read_file", {"ticker": T, "path": "facts/facts.md", "lines": 60})),
               think("Now the contract language in the 10-Q, since the thesis depends on it.", None,
                     ("search_file", {"ticker": T, "pattern": "take-or-pay",
                                      "path": "facts/filings/10-Q_2026-05-28_mdna.txt"})),
               think("Reporting the five numbers with sources.", None,
                     ("submit_result", {
                         "findings": "FY'26 revenue $133.2 bn against $37.4 bn (8-K 2026-09-30). 4Q'26 gross "
                                     "margin 86.8% (8-K). Strategic Customer Agreements are take-or-pay with "
                                     "fixed or banded prices (10-Q MD&A). Cash and investments $73.5 bn "
                                     "against $5.2 bn of debt (8-K). 1Q'27 guidance $61.5 bn (8-K).",
                         "figures": [{"label": "FY'26 revenue", "value": 133.19, "unit": "USD bn",
                                      "source": "8-K earnings release, 2026-09-30"}],
                         "open_questions": ["Share of FY'27 revenue under contract: not yet disclosed."],
                         "confidence": "high"})))

    # Quant: the associate builds the workbook and runs the simulations; the lead files the card
    llm.script("quant_lead",
               think("A DCF with a CAPM cost of capital, a multiples blend, three cases, then a Monte "
                     "Carlo. {quant_associate} builds and checks; I review and sign off.", None,
                     ("delegate", {"to": "quant_associate", "job": f"Build the {T} model with build_model, "
                                   "then run_simulations. Return the version, the three price targets, the "
                                   "formula check and the top value drivers."})),
               _model_card,
               think("Done.", "Micron model sent to Stott for approval."))
    llm.script("quant_associate",
               think("Building the workbook from assumptions.yaml, then the formula check against the engine.",
                     None, ("build_model", {"ticker": T})),
               think("Built and checked. Now two thousand simulations and the value drivers.", None,
                     ("run_simulations", {"ticker": T, "runs": 2000})),
               lambda p: _delta_reports(p, T))

    # Audit: real lookups of the approved model and the report text
    llm.script("audit_lead",
               think("First the approved numbers, then each one in the report.", None,
                     ("get_model", {"ticker": T})),
               lambda p: think("The base target in the cover and the summary must be the model's. Checking "
                               "the cover.", None,
                               ("search_file", {"ticker": T, "pattern": str(
                                   _last_tool_json(p).get("price_targets", {}).get("base", "")),
                                   "path": "sections/00_cover.md"}))(p),
               think("The bear and bull targets in the investment summary next.", None,
                     ("search_file", {"ticker": T, "pattern": "Bear Case Price Target|306.91",
                                      "path": "sections/01_investment_summary.md"})),
               think("The figures tie out. Reporting.", None,
                     ("report_to_captain", {"text": "Audit: the Micron report ties to the approved model. "
                                            "Base, bear and bull targets, the Underperform rating and the "
                                            "returns match, and nothing is left marked unverified."})),
               think("Done.", "Report checked against the model; no findings."))

    # Client Relations: the associate drafts from what is publishable, the lead finalizes
    issue: dict = {}

    def wren_drafts(params: dict) -> object:
        return think("Only the approved model's numbers go in: the targets, the rating and the cite line. "
                     "Everything else is from the filings.", None,
                     ("save_newsletter", newsletter(_last_tool_json(params))))(params)

    def wren_returns(params: dict) -> object:
        issue.update(_last_tool_json(params))
        if issue.get("errors"):   # the gate is real: if it ever blocks, the demo must not claim it passed
            raise RuntimeError(f"newsletter blocked by the publishing gate: {issue['errors']}")
        return think("The checks came back clean.", None,
                     ("submit_result", {"findings": f"Draft saved as issue {issue.get('issue')}: "
                                        f"{issue.get('words')} words, one X post and one LinkedIn post. "
                                        "No check errors.", "figures": [], "open_questions": [],
                                        "confidence": "high"}))(params)

    llm.script("cr_lead",
               think("A short note on Micron. {cr_associate} drafts from what we may publish; I check the "
                     "voice and the numbers, then finalize.", None,
                     ("delegate", {"to": "cr_associate", "job": "Draft the Micron note: start from "
                                   "newsletter_material, use the approved model's targets and rating "
                                   "exactly, no advice and no hype, and save it with save_newsletter."})),
               lambda p: think("Reading the draft before it goes anywhere.", None,
                               ("read_newsletter", {"issue": issue.get("issue", "")}))(p),
               lambda p: think("It says what we think and promises nothing. Building the files and filing "
                               "it for Stott.", None,
                               ("finalize_newsletter", {"issue": issue.get("issue", ""),
                                                        "note": "Micron initiation note, demo."}))(p),
               think("Done.", "The Micron note is filed for approval."))
    llm.script("cr_associate",
               think("First, what are we actually allowed to publish?", None, ("newsletter_material", {})),
               wren_drafts, wren_returns)

    # the wrap (each lead answers Juno in one line)
    for lead, line in (("er_lead", "Micron's earnings power is real; our call is about the price, and the report says why."),
                       ("quant_lead", "Three cases from one model: bear, base and bull, with the simulation range."),
                       ("cr_lead", "The client note is plain: the call, the range and what would change our mind.")):
        llm.script(lead, think("{chief_of_staff} wants one line.", None,
                               ("send_message", {"to": ["chief_of_staff"], "text": line})),
                   think("Sent.", "Replied to {chief_of_staff}."))


# ---- the run -----------------------------------------------------------------------------------
async def _pending_card(office: Office, kind: str) -> dict:
    while True:
        for c in office.store.approvals("pending"):
            if c["kind"] == kind:
                return c
        await asyncio.sleep(0.05)


async def run(office: Office, llm: DemoLLM) -> None:
    script(office, llm)
    office.captain_send("office", OPENING)
    card = await _pending_card(office, "model")          # Research and Quant work in parallel
    await office.idle()
    office.decide(card["id"], "approved", "Approved for the demo.")
    office.captain_send("office", APPROVE_NOTE)
    note = await _pending_card(office, "newsletter")     # Audit and Client Relations in parallel
    await office.idle()
    office.decide(note["id"], "approved", "Approved for the demo.")
    office.captain_send("office", WRAP_REQUEST)
    await office.idle()


def record(speed: float = 12.0) -> tuple[list[dict], Office]:
    """Run the scene once on a fresh throwaway database and return every event, in order."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{RECORD_DB}{suffix}").unlink(missing_ok=True)
    shutil.rmtree(RECORD_DIR, ignore_errors=True)
    events: list[dict] = []

    async def go() -> Office:
        llm = DemoLLM(speed=speed)
        office = Office(db_path=RECORD_DB, llm=llm, tone="rules", quant_dir=RECORD_DIR / "quant",
                        memory_dir=RECORD_DIR / "memory", outbox_dir=RECORD_DIR / "outbox")
        llm.office = office
        q = office.bus.subscribe()
        runner = asyncio.create_task(run(office, llm))
        while not runner.done() or not q.empty():
            try:
                events.append((await asyncio.wait_for(q.get(), 0.2)).as_dict())
            except TimeoutError:
                pass
        await runner   # surfaces any error in the scene
        return office

    office = asyncio.run(go())
    return events, office


def dump(events: list[dict], path: Path) -> None:
    path.write_text(json.dumps(events, default=str, indent=1))
