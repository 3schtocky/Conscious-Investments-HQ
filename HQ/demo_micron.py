"""The Micron demo (about five and a half minutes): one real initiation, start to finish, led by the Chief of Staff.

`record()` runs the scene once through the real engine on a throwaway database (the model workbook,
the simulations, the report lint, the audit lookups and the newsletter gate are all REAL tools on the
real Micron files in the Equity Research repo; only the agents' words are scripted) and captures every
event. `HQ.demo_script` then cuts that recording down to a timed, word-carrying script that each
visitor's browser plays back from 0:00. Nothing here calls the Anthropic API.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

from HQ.config import DATA_DIR
from HQ.demo import DemoLLM, _delta_reports, _last_tool_json, think
from HQ.engine.runtime import Office

T = "MU"
RECORD_DB = DATA_DIR / "micron-demo.db"
RECORD_DIR = DATA_DIR / "micron-demo"

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

We rate Micron (MU) Outperform. {cite} puts the base-case price target for MU at ${base:,.2f}, with a bear case of ${bear:,.2f} and a bull case of ${bull:,.2f}, against a share price of $1,062.29 when we built the model. The rating compares our base case with what we expect the S&P 500 to return over 15 months: 17.0% for MU against 10.1%, which clears our 5-point threshold by a narrow 6.9 points. Our base case takes the analysts' revenue, $274.9 bn in fiscal 2027 and $314.2 bn in fiscal 2028, and fades the margin from 83.0% to 65.0% by fiscal 2031 as new factories in Idaho, Singapore and Taiwan add supply from the middle of calendar 2027. The shares have risen 272.4% since the start of the year, but they still trade at 5.9x the earnings analysts expect for the next twelve months, so the market is treating these profits as temporary.

## How wide the range is

The distance between the bear and bull cases is the point. In the bear case for MU, prices reset as new capacity arrives and revenue falls to $110.0 bn in fiscal 2028, a loss of 71.1% from today's price. In the bull case, supply stays short and the customer agreements hold the gross margin near 86% for years. Memory has always moved in cycles: in fiscal 2023 the company's revenue fell 49.5% and its gross margin turned negative. The new agreements may soften the next downturn. They do not remove it, and their price floors have not been published in full. That is why an idea like this needs a position size that survives the bear case.

## What would change our mind

Three things. A disclosed floor price that implies a gross margin below 70.0% through fiscal 2028. Two quarters in a row of revenue below $60.0 bn. Or a share price above about $1,080, where the base-case return on our target for MU falls below the hurdle for our top rating.

## How this note was made

Every figure comes from a filing or from the approved model, and the office checked the report against the model before this note was written. Our price targets are produced by the model, not by the people writing about it. This is research, not personal advice, and it is a demonstration of how the office works.
"""
    x = (f"Our Micron (MU) initiation: Outperform, base-case target ${base:,.2f}, bear ${bear:,.2f}, bull "
         f"${bull:,.2f} ({cite.split(',')[0]}). A demonstration of how the office works, not investment advice.")
    linkedin = (f"We have initiated coverage of Micron (MU). We rate MU Outperform, with a base-case price "
                f"target of ${base:,.2f} for MU, a bear case of ${bear:,.2f} and a bull case of ${bull:,.2f}. "
                "Revenue grew from $37.4 bn to $133.2 bn in fiscal 2026 and the stock has already risen 272.4% "
                "this year, so the question is how long the margin lasts. The note explains our range and what "
                "would change our mind. This is research, not personal advice, and a demonstration of how "
                "the office works.")
    return {"title": "Micron (MU): why we rate it Outperform (demo)", "body": body, "x_post": x,
            "linkedin_post": linkedin}


# ---- what Stott says --------------------------------------------------------------------------
def opening(office: Office) -> str:
    n = office.name
    return (f"{n('chief_of_staff')}, we should initiate coverage on Micron (MU). I want the full report, a "
            f"bull, base and bear model for it, and {n('er_lead')} and {n('quant_lead')} working together on it.")


def approve_note(office: Office) -> str:
    n = office.name
    return (f"Approved. Have {n('audit_lead')} check the report against that model, and ask {n('cr_lead')} "
            "for a short note to clients on it.")


def buy_question(office: Office) -> str:
    n = office.name
    return (f"Good. Now the real question, {n('chief_of_staff')}: do we own it? I am thinking ten percent of the "
            f"portfolio. Ask {n('er_lead')} for a proposal, {n('audit_lead')} to check the rules, and "
            f"{n('quant_lead')} what the bear case means at that size.")


def approve_position(office: Office) -> str:
    return (f"Approved. Open Micron at ten percent, {office.name('chief_of_staff')}, and tell me where we "
            "stand.")


def wrap_request(office: Office) -> str:
    return f"Thank you all. {office.name('chief_of_staff')}, pull the leads together for a one-line wrap."


# ---- scripted agents, one act at a time ---------------------------------------------------------
# Each act loads only its own turns, just before it runs, so no task can use up a later act's lines.
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


def act_open(llm: DemoLLM) -> None:
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
               think("Both desks have it.", "Research and Quant are on Micron."))
    llm.script("er_lead",
               think("The facts pack and the filings are in coverage/MU. {er_associate} pulls the key "
                     "facts so I can check every figure in the report against a source.", None,
                     ("delegate", {"to": "er_associate", "job": "Read facts/facts.md and the 8-K earnings "
                                   "release for Micron. Return the five numbers the thesis rests on, "
                                   "each with its source."})),
               think("The facts agree with the draft. Now the style and sourcing lint on all eleven "
                     "sections.", None, ("erb_lint", {"ticker": T})),
               think("Lint is clean. The report goes to Stott with the model.", None,
                     ("report_to_captain", {"text": "The Micron initiation is written: eleven sections, every "
                                            "figure tagged to a filing or the model, style and sourcing "
                                            "checks clean. The price targets come from {quant_lead}'s model."})),
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


def act_review(llm: DemoLLM) -> None:
    """Stott has approved the model: Audit checks the report against it, Client Relations writes the note."""
    llm.script("quant_lead",   # the system tells Sigma the card was approved
               think("Approved, so these are the firm's numbers now.", "Noted: the Micron model is official."))
    llm.script("chief_of_staff",
               think("Stott approved the model. Audit checks the report against it and Client Relations "
                     "writes the note, in parallel.", None,
                     ("assign_task", {"to": "audit_lead", "title": "Check the Micron report against the model",
                                      "brief": "Check that every price target, rating and return in the Micron "
                                               "report matches the approved model. Report any mismatch."}),
                     ("assign_task", {"to": "cr_lead", "title": "Micron client note",
                                      "brief": "Write a short note to clients on the Micron initiation, using only "
                                               "the approved model's numbers. File it for Stott."})),
               think("Both are under way.", "Audit and Client Relations have it."))
    llm.script("audit_lead",
               think("First the approved numbers, then each one in the report.", None,
                     ("get_model", {"ticker": T})),
               lambda p: think("The base target in the cover and the summary must be the model's. Checking "
                               "the cover.", None,
                               ("search_file", {"ticker": T, "pattern": f"{_last_tool_json(p).get('price_targets', {}).get('base', 0):,.2f}",
                                                "path": "sections/00_cover.md"}))(p),
               think("The bear and bull targets in the investment summary next.", None,
                     ("search_file", {"ticker": T, "pattern": "Bear Case Price Target|306.91",
                                      "path": "sections/01_investment_summary.md"})),
               think("The figures tie out. Reporting.", None,
                     ("report_to_captain", {"text": "Audit: the Micron report ties to the approved model. "
                                            "Base, bear and bull targets, the Outperform rating and the "
                                            "returns match, and nothing is left marked unverified."})),
               think("Done.", "Report checked against the model; no findings."))
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


def _bear_at_size(params: dict) -> object:
    m = _last_tool_json(params)
    r = m.get("total_returns", {})
    lose, base, bull = (0.10 * r.get(k, 0) * 100 for k in ("bear", "base", "bull"))
    text = (f"At ten percent, the bear case (target ${m['price_targets']['bear']:,.2f}) costs the portfolio "
            f"{abs(lose):.1f}% of its value, the base case adds {base:.1f}% and the bull case adds {bull:.1f}% "
            "(our arithmetic: ten percent times each case's return). The downside is real but bounded, and "
            "it is the reason I would not go above ten.")
    return think("Sizing has to be set by the bear case, not the base case. Ten percent times each return "
                 "gives the portfolio impact.", None, ("report_to_captain", {"text": text}))(params)


def _propose(params: dict) -> object:
    m = _last_tool_json(params)
    pt = m["price_targets"]
    thesis = (f"Quant model v{m['version']} rates Micron (MU) Outperform with a base target of ${pt['base']:,.2f}, "
              f"{m['total_returns']['base'] * 100:.1f}% above the price, against a bull case of ${pt['bull']:,.2f} "
              f"and a bear case of ${pt['bear']:,.2f}. Why now: revenue is growing into the Street's FY'27 and "
              "FY'28 numbers while the shares trade at 5.9x NTM EPS. What proves it wrong: two quarters of "
              "revenue below $60.0 bn, or a disclosed contract floor implying a gross margin below 70.0%. "
              "Why ten percent: this is above our usual 3, 5 and 8 percent tiers, so it is a demo-only size "
              "that goes to Stott as an explicit decision; the bear case would cost 7.1% of the portfolio.")
    return think("The model qualifies it, so I propose the position with the risk and the size argued.", None,
                 ("propose_position", {"ticker": T, "size_pct": POSITION_PCT, "thesis": thesis}))(params)


def _position_report(params: dict) -> object:
    p = (_last_tool_json(params).get("open_positions") or [{}])[0]
    s = _last_tool_json(params)
    text = (f"Micron is open: {p.get('size_pct')}% of the paper portfolio, ${s.get('value_usd', 0) * p.get('size_pct', 0) / 100:,.2f} "
            f"at ${p.get('entry_price', 0):,.2f}, entered {p.get('entered')}. The base target is "
            f"${p.get('base_target', 0):,.2f} and the rating is {p.get('rating')}. No real money moves; "
            "it is a paper position and it is scored against the S&P 500 from today.")
    return think("The fill is in. One clear line for Stott with the size, the price and the target.", None,
                 ("report_to_captain", {"text": text}))(params)


def act_buy(llm: DemoLLM) -> None:
    """Stott asks whether to own it: proposal, rules check and downside, then his decision."""
    llm.script("chief_of_staff",
               think("Three jobs in parallel: the proposal, the rule check and the bear case at ten percent.",
                     None,
                     ("assign_task", {"to": "er_lead", "title": "Micron position proposal",
                                      "brief": "Propose a paper-portfolio position in Micron (MU) at ten percent, "
                                               "with the case, the risk and why that size."}),
                     ("assign_task", {"to": "audit_lead", "title": "Check the Micron position against the rules",
                                      "brief": "Check that Micron qualifies for the paper portfolio under the "
                                               "rules and say plainly where a ten percent size departs from them."}),
                     ("assign_task", {"to": "quant_lead", "title": "Micron bear case at ten percent",
                                      "brief": "State what the bear, base and bull cases mean for the portfolio "
                                               "at a ten percent position."})),
               think("All three are on it.", "{er_lead}, {audit_lead} and {quant_lead} are on the position."),
               # the second Juno task: Stott's decision
               think("The position is in. Reading the portfolio so the numbers are exact.", None,
                     ("read_portfolio", {})),
               _position_report,
               think("Done.", "Micron position confirmed to Stott."))
    llm.script("er_lead",
               think("First what we hold, then the approved model.", None, ("read_portfolio", {})),
               think("The firm's numbers for Micron, from the approved model.", None, ("get_model", {"ticker": T})),
               _propose,
               think("Done.", "Position proposal filed for Stott."),
               think("Approved, so the position is open.", "Noted: the Micron position is open."))
    llm.script("audit_lead",
               think("The rules: an approved model that rates it Outperform, one position per name.", None,
                     ("get_model", {"ticker": T})),
               think("And what the portfolio holds today.", None, ("read_portfolio", {})),
               think("It qualifies, with one departure to flag.", None,
                     ("report_to_captain", {"text": "Audit: Micron qualifies. The approved model v1 rates it "
                                            "Outperform, the portfolio holds no Micron today and has the cash. "
                                            "One departure: ten percent is above the usual 3, 5 and 8 percent "
                                            "tiers, so it is your decision, Stott, not a standard size."})),
               think("Done.", "Micron checked against the portfolio rules."))
    llm.script("quant_lead",
               think("The approved cases first.", None, ("get_model", {"ticker": T})),
               _bear_at_size,
               think("Done.", "Bear case at ten percent reported."))


def act_wrap(llm: DemoLLM) -> None:
    def juno_wrap(params: dict) -> object:
        m = _last_tool_json(params)
        pt = m.get("price_targets", {})
        text = (f"Micron is done. The report, the model, the client note and the position are in. Rating "
                f"{m.get('rating')}: base ${pt.get('base', 0):,.2f}, bear ${pt.get('bear', 0):,.2f}, bull "
                f"${pt.get('bull', 0):,.2f} ({m.get('cite_as')}). Audit tied the report to the model, the "
                f"note passed the publishing checks, and Micron is held at {POSITION_PCT}% of the paper portfolio.")
        return think("All four pieces are done and the numbers match. One report to Stott.", None,
                     ("report_to_captain", {"text": text}))(params)

    llm.script("chief_of_staff",
               think("A one-line wrap from each lead, then one report for Stott.", None,
                     ("send_message", {"to": ["er_lead", "quant_lead", "cr_lead"],
                                       "text": "Wrap on Micron: one line each on what you would tell a client."})),
               think("Reading the approved model so the wrap carries the exact numbers.", None,
                     ("get_model", {"ticker": T})),
               juno_wrap,
               think("Done.", "Micron wrapped for Stott."))
    for lead, line in (("er_lead", "Micron's earnings power is real, and our call is that the price still leaves room; the report says why."),
                       ("quant_lead", "Three cases from one model: bear, base and bull, with the simulation range."),
                       ("cr_lead", "The client note is plain: the call, the range and what would change our mind.")):
        llm.script(lead, think("{chief_of_staff} wants one line.", None,
                               ("send_message", {"to": ["chief_of_staff"], "text": line})),
                   think("Sent.", "Replied to {chief_of_staff}."))


def act_note_approved(llm: DemoLLM) -> None:
    llm.script("cr_lead", think("Approved, so it goes out as drafted.", "The Micron note is approved."))


# ---- the run -----------------------------------------------------------------------------------
POSITION_PCT = 10   # demo-only: above the portfolio's usual 3, 5 and 8 percent tiers


async def _pending_card(office: Office, kind: str) -> dict:
    while True:
        for c in office.store.approvals("pending"):
            if c["kind"] == kind:
                return c
        await asyncio.sleep(0.05)


def _spy_price() -> float:
    try:
        from HQ import quotes

        return float(quotes.latest(["SPY"]).get("SPY") or 0) or 650.0
    except Exception:   # noqa: BLE001 - offline: a fixed benchmark price still scores the entry
        return 650.0


async def run(office: Office, llm: DemoLLM) -> None:
    """The story, with Stott's three decisions made by code. Every act waits for the office to go quiet."""
    from HQ import portfolio

    prices = {T: MU_PRICE, "SPY": _spy_price()}
    act_open(llm)
    office.captain_send("office", opening(office))
    model_card = await _pending_card(office, "model")
    await office.idle()

    llm.scripts.clear()
    act_review(llm)
    office.decide(model_card["id"], "approved", "Approved for the demo.")
    office.captain_send("office", approve_note(office))
    note_card = await _pending_card(office, "newsletter")
    await office.idle()

    llm.scripts.clear()
    act_note_approved(llm)
    act_buy(llm)
    office.decide(note_card["id"], "approved", "Approved for the demo.")
    await office.idle()
    office.captain_send("office", buy_question(office))
    position_card = await _pending_card(office, "portfolio")
    await office.idle()
    office.decide(position_card["id"], "approved", "Approved for the demo.", prices=prices)
    office.captain_send("office", approve_position(office))
    await office.idle()
    assert office.store.positions("open"), "the position did not open"
    portfolio.mark(office, prices)

    llm.scripts.clear()
    act_wrap(llm)
    office.captain_send("office", wrap_request(office))
    await office.idle()


MU_PRICE = 1062.29   # the price the report and the model are dated at (2026-10-05 close)


def record(speed: float = 12.0) -> tuple[list[dict], Office]:
    """Run the scene once on a fresh throwaway database and return every event, in order."""
    from HQ import portfolio, quotes

    for suffix in ("", "-wal", "-shm"):
        Path(f"{RECORD_DB}{suffix}").unlink(missing_ok=True)
    shutil.rmtree(RECORD_DIR, ignore_errors=True)
    events: list[dict] = []
    spy = _spy_price()
    real_latest, real_sizes = quotes.latest, portfolio.SIZES

    def fixed_quotes(tickers: list[str]) -> dict:
        return {t: {T: MU_PRICE, "SPY": spy}.get(t) for t in tickers}

    quotes.latest = fixed_quotes                        # the demo is dated at the report's close, not today's tape
    portfolio.SIZES = (*real_sizes, POSITION_PCT)       # demo-only tier; the real office keeps 3, 5 and 8

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

    try:
        office = asyncio.run(go())
    finally:
        quotes.latest, portfolio.SIZES = real_latest, real_sizes
    return events, office


def dump(events: list[dict], path: Path) -> None:
    path.write_text(json.dumps(events, default=str, indent=1))
