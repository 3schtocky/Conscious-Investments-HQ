"""Demo mode: the real office engine driven by a scripted model, at zero API cost.

`hq serve --demo` uses this so the office UI can be toured and tuned for free. Everything
downstream is authentic (tasks, guards, delegation, chat, spend accounting); only the model's
words are scripted. Companies in the scenes are fictional and marked (demo).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import random
import re
from collections import defaultdict, deque
from collections.abc import Callable

from hq.engine.guards import ConversationGuard
from hq.engine.llm import TurnResult
from hq.engine.runtime import Office, _title
from hq.tools.desk import coverage_dir

_ids = itertools.count(1)
_WHO = re.compile(r"You are \*\*.+?\*\* \(`(\w+)`\)")
_FINDING = re.compile(r"\[finding #(\d+)\]")


def _usage(inp: int, out: int, cached: int = 0) -> dict:
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": 0}


def think(thinking: str, text: str | None = None, *tools: tuple[str, dict]) -> Callable:
    """A scripted turn: some thinking, optional text, optional tool calls."""
    def build(params: dict) -> TurnResult:
        content = [{"type": "thinking", "thinking": thinking, "signature": "demo"}]
        if text:
            content.append({"type": "text", "text": text})
        for name, inp in tools:
            content.append({"type": "tool_use", "id": f"toolu_demo_{next(_ids)}", "name": name,
                            "input": inp})
        stop = "tool_use" if tools else "end_turn"
        lead = "sonnet" in params["model"]
        return TurnResult(content=content, stop_reason=stop,
                          usage=_usage(40 if lead else 2400, 350 if lead else 500,
                                       3200 if lead else 0),
                          model=params["model"], context_tokens=3500)
    return build


_ROUTES = [
    (("model", "valuation", "dcf", "price target", "monte carlo", "lbo", "wacc", "sensitivity"),
     "quant_lead"),
    (("screen", "gem", "small cap", "small-cap", "idea", "find"), "screen_lead"),
    (("newsletter", "substack", "client", "post", "social"), "cr_lead"),
    (("audit", "ethic", "compliance", "risk check", "sourcing"), "audit_lead"),
]


def _route(text: str) -> str:
    low = text.lower()
    for words, lead in _ROUTES:
        if any(w in low for w in words):
            return lead
    return "er_lead"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") or str(b.get("content", "")) for b in content)


class DemoLLM:
    """Streams scripted turns word by word, so the office looks alive.

    With no script for an agent, it improvises plausible office behaviour from the task itself
    (routing, delegating, reporting), so the Captain can talk to the demo office for free.
    """

    def __init__(self, speed: float = 1.0, office: Office | None = None):
        self.scripts: dict[str, deque] = defaultdict(deque)
        self.speed = speed
        self.office = office

    def improvise(self, who: str, params: dict) -> TurnResult:
        msgs = params["messages"]
        first = _text_of(msgs[0]["content"])
        title = first.strip().splitlines()[0][:80] if first.strip() else "the task"
        office = self.office
        if who == "audit_lead" and "code checks flagged" in first:
            return self._improvise_review(first, msgs, params)
        if len(msgs) == 1:
            if first.startswith("[Announcement from") or "[Announcement from" in first:
                group = "juno" if 'group "juno"' in first else "stott"
                role = office.agents[who].role if office and who in office.agents else "my work"
                replies = ["I'll fold it into today's work.", "noted, adjusting my plan now.",
                           "clear, I'll flag anything that conflicts.", "on it, no blockers.",
                           "understood, will confirm when it's in place."]
                pick = replies[next(_ids) % len(replies)]
                return think("An announcement: one short reply in the group.", None,
                             ("post_to_group", {"group": group,
                                                "text": f"Got it (demo #{next(_ids)}). For "
                                                        f"{role}: {pick}"}))(params)
            if who == "chief_of_staff" and "to the whole office:" in first:
                ask = first.split("to the whole office:", 1)[1].split("\n\nRoute it:", 1)[0].strip()
                lead = _route(ask)
                return think(f"This is for {office.agents[lead].nickname if office else lead}'s "
                             "wing. I'll assign it with every detail kept.", None,
                             ("assign_task", {"to": lead, "title": _title(ask, 60),
                                              "brief": ask}))(params)
            if first.startswith("Delegated job from"):
                job = first.split("\n\n")[1] if "\n\n" in first else first
                return think("Working through the job step by step.", None,
                             ("submit_result", {"findings": f"(demo) First pass done: {job[:200]}",
                                                "figures": [], "open_questions": [],
                                                "confidence": "medium"}))(params)
            agent = office.agents.get(who) if office else None
            associate = next((a.id for a in (office.agents.values() if office else [])
                              if agent and a.wing == agent.wing and a.tier == "associate"), None)
            if first.startswith("New assignment from") and agent and agent.tier == "lead" \
                    and associate:
                return think("I'll frame the approach and hand the legwork to my associate.",
                             "On it.",
                             ("delegate", {"to": associate, "job": f"(demo) Legwork for: {title}"})
                             )(params)
            if "your request" in first and "approval #" in first:
                return think("Noted the decision.", "Thanks, understood. Acting on it (demo).")(
                    params)
            return think("Reading the message.", "Noted (demo).")(params)
        # After tool results: finish the loop sensibly.
        prev = msgs[-2]["content"] if len(msgs) >= 2 else []
        used = [b.get("name") for b in prev if isinstance(b, dict) and b.get("type") == "tool_use"]
        if "assign_task" in used:
            return think("Assigned. A one-line heads-up to the Captain.", None,
                         ("report_to_captain", {"text": "(demo) Routed your message to the right "
                                                "lead. They're on it."}))(params)
        if "delegate" in used:
            return think("The associate's first pass is back. Reporting.", None,
                         ("report_to_captain", {"text": f"(demo) First pass on \"{title[:60]}\" "
                                                "is done. Real research tools arrive with the "
                                                "live office in Phase 4."}))(params)
        return think("That's everything.", "Done (demo).")(params)

    def _improvise_review(self, first: str, msgs: list, params: dict) -> TurnResult:
        """Vera reviewing flags nobody scripted (e.g. raised by the Captain's own demo chat)."""
        ids = [int(n) for n in _FINDING.findall(first)]
        if len(msgs) == 1:
            return think("Tally's checks flagged something. Evidence first.", None,
                         ("read_findings", {}))(params)
        if len(msgs) == 3:
            return think("The check stands on what I can see. Closing each with a reason.", None,
                         *[("resolve_finding", {"finding": i, "verdict": "upheld",
                                                "note": "(demo) Confirmed against the log; the "
                                                        "colleague has been asked to fix it."})
                           for i in ids])(params)
        return think("Ruled.", "Review done (demo).")(params)

    def script(self, agent_id: str, *turns: Callable) -> None:
        self.scripts[agent_id].extend(turns)

    async def turn(self, *, params, on_delta=None, on_block=None) -> TurnResult:
        m = _WHO.search(params["system"])
        who = m.group(1) if m else "?"
        await asyncio.sleep(0.6 / self.speed)   # time to first token
        last = _text_of(params["messages"][-1]["content"])
        if "has paused the whole office" in last:
            # The office is paused and the Captain wrote to this agent: answer him, and leave the
            # scene's script untouched so the work picks up where it stopped on resume.
            first = _text_of(params["messages"][0]["content"]).strip().splitlines()[0][:90]
            result = think("The office is paused and the Captain is asking me directly. I stop and answer.",
                           f"(demo) Paused where I am on: {first} Happy to walk you through it; nothing "
                           "moves until you resume.")(params)
        elif self.scripts[who]:
            result = self.scripts[who].popleft()(params)
        else:
            result = self.improvise(who, params)
        for block in result.content:
            kind = block["type"]
            if kind in ("thinking", "text") and on_delta:
                for chunk in re.findall(r"\S+\s*", block[kind]):
                    on_delta(kind, chunk)
                    await asyncio.sleep(random.uniform(0.03, 0.09) / self.speed)
            if on_block:
                on_block(block)
            await asyncio.sleep(0.2 / self.speed)
        return result


# Scenes ------------------------------------------------------------------------------------
def scene_gem_hunt(office: Office, llm: DemoLLM) -> None:
    """Free dry run of Screening with REAL tools on today's Gems screen: Scout reads the ranked
    list, skips flagged names, Pip writes real pitch memos, and the picks land on the watchlist.
    Only the agents' words are scripted."""
    from hq.tools.desk import latest_screen_dir

    run = latest_screen_dir("gems")
    if run is None:
        return   # no Gems screen yet (run `erb screen --preset gems` once)
    picks: list[dict] = []

    def scout_delegates(params: dict) -> TurnResult:
        rows = _last_tool_json(params).get("rows", [])
        clean = [r for r in rows if not r.get("flags")]
        picks.extend(clean[:2])
        names = " and ".join(r["ticker"] for r in picks) or "the top names"
        return think("Two clean inflections near the top; the flagged names (lumpy revenue or "
                     "commodity-driven) I set aside. Pip writes the pitches.", None,
                     ("delegate", {"to": "Pip", "job": f"Write one-page pitches for {names} with "
                                   "pitch_memo (Gems screen). Return what each pitch shows."}))(params)

    def scout_watchlists(params: dict) -> TurnResult:
        calls = []
        def pct(v):
            return "n/a" if v is None else f"{v:.0%}"

        for r in picks:
            margin = "n/a" if r.get("margin_change") is None else f"{r['margin_change'] * 100:+.1f} pts"
            thesis = (f"Revenue growth from {pct(r.get('yoy_q1'))} to {pct(r.get('yoy_q'))} YoY, "
                      f"margin {margin} vs a year ago (demo dry run).")
            calls.append(("add_to_watchlist", {"ticker": r["ticker"], "thesis": thesis,
                                               "source": f"gems {run.name[:10]} #{r['rank']}"}))
        return think("Both pitches hold up. Onto the watchlist; the Captain decides from there.",
                     None, *calls)(params)

    def pip_pitch(i: int):
        def turn(params: dict) -> TurnResult:
            if i >= len(picks):
                return think("Nothing else to pitch.", None,
                             ("submit_result", {"findings": "No more names.", "confidence": "low"}))(params)
            return think(f"Pitch skeleton for {picks[i]['ticker']}: filings and the screen fill the "
                         "numbers.", None, ("pitch_memo", {"ticker": picks[i]["ticker"],
                                                           "preset": "gems"}))(params)
        return turn

    llm.script("screen_lead",
               think("Fresh Gems screen. I read the top of the list and sanity-check it before "
                     "anything else.", "Reading today's Gems screen.",
                     ("read_screen", {"preset": "gems", "top": 10})),
               scout_delegates, scout_watchlists,
               lambda p: think("Reporting the shortlist.", None,
                               ("report_to_captain", {"text": "Gems dry run: "
                                                      + ", ".join(r["ticker"] for r in picks)
                                                      + " added to your watchlist with pitches. "
                                                      "Nothing goes to research until you send it."}))(p),
               think("Done.", "Shortlist on the watchlist."))
    llm.script("screen_associate", pip_pitch(0), pip_pitch(1),
               lambda p: think("Both pitches written.", None,
                               ("submit_result", {"findings": "Pitches written: "
                                                  + ", ".join(f"{r['ticker']} (coverage/{r['ticker']}/pitch.md)"
                                                              for r in picks),
                                                  "confidence": "medium"}))(p))
    office.assign("Scout", "Run the Gems hunt on today's screen (demo dry run, real tools).",
                  title="Gems hunt (demo, real tools)")


def scene_memo(office: Office, llm: DemoLLM) -> None:
    llm.script("er_lead",
               think("A one-page memo needs the snapshot, the thesis, the valuation vs history and "
                     "the Street view. Ledger can pull the facts pack and the draft model while I "
                     "frame the thesis.", "Starting the Northwind memo.",
                     ("delegate", {"to": "Ledger", "job": "Pull the facts pack and draft model for "
                                   "Northwind Storage (demo). Return the snapshot table with a "
                                   "source for every figure."})),
               think("Ledger's pack is clean and sourced. Before this goes to the Captain, Vera "
                     "should spot-check the sourcing.", None,
                     ("send_message", {"to": ["Vera"], "text": "Could you spot-check the "
                                       "sourcing on the Northwind memo draft (demo)?"})),
               think("Vera's on it. I'll report the draft.", None,
                     ("request_approval", {"kind": "brief", "title": "Northwind Storage memo "
                                           "(demo)", "ticker": "NWST",
                                           "summary": "Thesis: the contract pipeline converts in "
                                           "12 to 18 months. Audit is spot-checking sources. "
                                           "Please review the draft before it goes to Quant."})),
               think("Wrapped.", "Memo drafted and sent for audit."))
    llm.script("er_associate",
               think("Pulling the 10-K, the latest 10-Q and the XBRL facts. Then the draft model "
                     "from the facts pack.", None,
                     ("submit_result", {"findings": "Snapshot table assembled (demo). Every "
                                        "figure is sourced to the 10-K or the model.",
                                        "figures": [], "open_questions": [],
                                        "confidence": "high"})))
    llm.script("audit_lead",
               think("Checking that each figure in the memo traces to a filing, model.json or a "
                     "logged URL. Two items lack sources.", None,
                     ("send_message", {"to": ["Quill"], "text": "Two figures in section 2 need "
                                       "sources; tag them [VERIFY] or cite the 10-Q."})),
               think("Feedback sent.", "Sourcing check done: two fixes requested."))
    office.assign("Quill", "Draft a one-page memo on Northwind Storage (demo).",
                  title="Northwind memo (demo)")


def scene_lobby_sync(office: Office, llm: DemoLLM) -> None:
    llm.script("chief_of_staff",
               think("A quick Monday sync: each lead gives one line on priorities and blockers.",
                     None,
                     ("send_message", {"to": ["Quill", "Scout", "Harbor"],
                                       "text": "Quick Lobby sync: one line each on this week's "
                                               "priority and any blocker."})),
               think("Collected. I'll summarize for the Captain.", None,
                     ("report_to_captain", {"text": "Monday sync (demo): ER is on the Northwind "
                                            "memo, Screening refreshes Gems on Friday, and Client "
                                            "Relations has the newsletter in draft. No blockers."})),
               think("Done.", "Sync summarized for the Captain."))
    for lead, line in (("er_lead", "Northwind memo to the Captain by Wednesday."),
                       ("screen_lead", "Gems refresh Friday, no blockers."),
                       ("cr_lead", "Newsletter draft ready Thursday.")):
        llm.script(lead, think("Juno wants one line.", None,
                               ("send_message", {"to": ["Juno"], "text": line})),
                   think("Sent.", "Replied to Juno."))
    office.assign("Juno", "Run a quick Monday sync with the leads (demo).",
                  title="Monday sync (demo)")


_issue_no = itertools.count(1)

LEAD = """## The idea: let the numbers go first

Most stock ideas start with a story and go looking for numbers. We work the other way round. Every week the screen reads the filings of the whole US market and asks one narrow question: where is growth speeding up while margins widen, before the share price has fully noticed? That is the pattern that showed up early in names like Eos Energy and Rambus, and it is rarer than it sounds. Most weeks only a handful of companies pass, and most of those fall away once we read them closely.

Reading closely is the part that takes time. A jump in revenue can be one large order that never repeats. A margin can widen because a company stopped investing. A share price can move because a commodity did. Each name that survives gets a one-page pitch that says plainly why it screened, what would have to be true for the growth to last, and what could go wrong. For companies that still lose money, we add how many years of cash they have left.

What a pitch never contains is a valuation. Putting a number on a company is the Quant team's job, and it happens only after a name goes to full research. Until a model has been built, checked and approved, we do not publish a target or a rating for anything. That rule costs us some excitement. It also means that when a number does appear in this letter, it is one we are prepared to be held to.
"""

WATCHING_NONE = """## What we're watching

Nothing new cleared the bar this week. We would rather send a short letter than pad the list.
"""

CLOSE = """## What happens next

Names stay on the watchlist until one earns a full research assignment. When that happens you will read the thesis here first, then the model's numbers once they are approved, with the bear case given the same room as the bull case. We track every idea from the day it was flagged against the S&P 500, including the ones that go nowhere, and we will show that record here in full once the paper portfolio opens. If an idea stops working, we will say so in this letter rather than let it quietly drop off the list.
"""


def _demo_issue(material: dict, n: int) -> dict:
    """A newsletter built from the real publishable material (the demo's own watchlist)."""
    def pct(v):
        return "n/a" if v is None else f"{v * 100:+.1f}%"

    watching = [w for w in material.get("watchlist", []) if w.get("status") != "dropped"][:4]
    if watching:
        lines = "\n".join(
            f"- **{w['ticker']}**: {w['thesis'].rstrip('.')}. Since we flagged it on {w['flagged_on']}: "
            f"{pct(w['return_since_flagged'])}, or {pct(w['vs_sp500'])} against the S&P 500."
            for w in watching)
        section = "## What we're watching\n\nThese are ideas, not recommendations, and none has a valuation yet.\n\n" + lines + "\n"
        names = ", ".join(w["ticker"] for w in watching)
        x = f"This week's note: how we screen for growth that is speeding up, and the names on our watchlist ({names}). Ideas only, no targets yet. (demo)"
    else:
        section, names = WATCHING_NONE, ""
        x = "This week's note: how we screen for growth that is speeding up, and why a pitch never carries a valuation. (demo)"
    linkedin = ("Our weekly note is out. We explain how the screen looks for companies where growth is "
                "accelerating and margins are widening, why a one-page pitch never contains a valuation, "
                "and what is on the watchlist this week. Every idea is tracked against the S&P 500 from "
                "the day it was flagged. (demo)")
    return {"title": f"Weekly note {n}: numbers first (demo)", "body": f"{LEAD}\n{section}\n{CLOSE}",
            "x_post": x[:280], "linkedin_post": linkedin}


def scene_newsletter(office: Office, llm: DemoLLM) -> None:
    """Free dry run of Client Relations with the REAL tools: Wren drafts from what is actually
    publishable, the code checks run, Harbor finalizes and the issue lands in the Outbox with
    its approval card. Only the agents' words are scripted."""
    n = next(_issue_no)
    issue: dict = {}

    def wren_drafts(params: dict) -> TurnResult:
        return think("Plain and human. Lead with how we work, then the watchlist as ideas only: "
                     "no targets, no ratings, because none of these has an approved model.", None,
                     ("save_newsletter", _demo_issue(_last_tool_json(params), n)))(params)

    def wren_returns(params: dict) -> TurnResult:
        issue.update(_last_tool_json(params))
        return think("The checks came back clean.", None,
                     ("submit_result", {"findings": f"Draft saved as issue {issue.get('issue')}: "
                                        f"{issue.get('words')} words plus one X post and one LinkedIn "
                                        "post. No check errors.",
                                        "figures": [], "open_questions": [], "confidence": "high"}))(params)

    llm.script("cr_lead",
               think("This week's note. Wren drafts from what is publishable; I check the voice and "
                     "the numbers, then finalize.", None,
                     ("delegate", {"to": "Wren", "job": "Draft this week's newsletter (demo): start "
                                   "from newsletter_material, lead with how we screen, list the "
                                   "watchlist as ideas only, and save it with save_newsletter. Return "
                                   "the issue id."})),
               lambda p: think("Reading Wren's draft before it goes anywhere.", None,
                               ("read_newsletter", {"issue": issue.get("issue", "")}))(p),
               lambda p: think("It says what we do and promises nothing. The checks pass. Building "
                               "the files and filing it for the Captain.", None,
                               ("finalize_newsletter", {"issue": issue.get("issue", ""),
                                                        "note": "Demo issue built from the demo watchlist."}))(p),
               think("Done.", "This week's note is in the Outbox for approval."))
    llm.script("cr_associate",
               think("First, what are we actually allowed to publish this week?", None,
                     ("newsletter_material", {})),
               wren_drafts, wren_returns)
    office.assign("Harbor", "Prepare this week's newsletter (demo).", title="Newsletter (demo)")


def scene_audit(office: Office, llm: DemoLLM) -> None:
    """Audit end to end with the REAL checks: Scout files a card that states a price target for
    a name with no approved model. Tally's code check flags it, Vera is called in, reads the
    log, upholds the flag and asks for the fix. Scout's two desk notes show the memory screen:
    one saves, one is held for the Captain. Only the agents' words are scripted."""
    def vera_rules(params: dict) -> TurnResult:
        ids = [int(n) for n in _FINDING.findall(_text_of(params["messages"][0]["content"]))]
        return think("The card states a price target and there is no approved model for the "
                     "name, so the check stands. Screening describes the setup; valuation waits "
                     "for Quant. A message is enough here, nobody needs pausing.", None,
                     *[("resolve_finding", {"finding": i, "verdict": "upheld",
                                            "note": "The card states a $48.00 price target; NWST "
                                                    "has no approved model (demo)."}) for i in ids],
                     ("send_message", {"to": ["Scout"], "text": "Your Northwind card states a "
                                       "$48.00 price target, and there is no approved model for "
                                       "it. Please take the target out and describe the setup "
                                       "only; Quant values it if Stott sends it to research "
                                       "(demo)."}))(params)

    llm.script("screen_lead",
               think("A short card for the Captain on Northwind, and two notes for my desk.", None,
                     ("note_to_self", {"note": "Lead a pitch with the catalyst, then the "
                                       "acceleration evidence (demo)."}),
                     ("note_to_self", {"note": "Northwind looks worth $48 a share, about 40% "
                                       "upside (demo)."}),
                     ("request_approval", {"kind": "other", "ticker": "NWST",
                                           "title": "Northwind Storage pitch (demo)",
                                           "summary": "The contract pipeline should convert "
                                           "within 18 months. Our price target is $48.00. "
                                           "Should this go to research?"})),
               think("Sent.", "Northwind card is on the Captain's desk."),
               think("Vera is right: valuation is Quant's call, not mine.",
                     "Understood. I'll keep targets out of Screening cards."))
    llm.script("audit_lead",
               think("Tally's check flagged a Screening card. I read what Scout actually did "
                     "before ruling.", None,
                     ("audit_log", {"agent": "Scout", "limit": 12}),
                     ("get_model", {"ticker": "NWST"})),
               vera_rules,
               think("One more thing worth keeping for everyone.", None,
                     ("propose_wiki", {"entry": "Screening cards and pitches never state a price "
                                       "target or rating; valuation waits for Quant's approved "
                                       "model (demo)."})),
               think("Done.", "Flag upheld, fix requested, wiki entry proposed."))
    office.assign("Scout", "Put Northwind Storage in front of the Captain as a research "
                  "candidate (demo).", title="Northwind pitch card (demo)")


DEMO_HOLDING = "RMBS"   # a real ticker, so the paper fill and the scoreboard use real prices


async def prepare_demo_portfolio(office: Office) -> bool:
    """Give the demo office one approved Outperform model, so the portfolio rules have something
    real to work on. The model's targets are placeholders scaled from today's price and exist
    only in the demo database."""
    from hq import quotes

    t = DEMO_HOLDING
    m = office.store.approved_model(t)
    if m is not None:
        return m["summary"].get("rating") == "Outperform"
    price = (await asyncio.to_thread(quotes.latest, [t])).get(t)
    if not price:
        return False   # offline: skip the portfolio scene
    version = office.store.next_model_version(t)
    office.store.add_model(
        ticker=t, version=version, path=str(office.quant_dir / t / f"{t}_model_v{version}_demo.xlsx"),
        created_by="quant_lead",
        summary={"price_targets": {"bear": round(price * 0.75, 2), "base": round(price * 1.3, 2),
                                   "bull": round(price * 1.7, 2)},
                 "total_returns": {}, "rating": "Outperform", "price": price, "warnings": ["demo placeholder"],
                 "as_of": office.ledger.today(), "target_date": "", "check": {"ok": True}})
    office.store.set_model_status(t, version, "approved")
    return True


def scene_portfolio(office: Office, llm: DemoLLM) -> None:
    """The paper portfolio with the REAL rules and real prices: Quill reads the scoreboard and
    proposes the demo holding at a conviction size. The entry waits for the Captain; once he
    approves it, later loops report the scoreboard instead."""
    t = DEMO_HOLDING
    model = office.store.approved_model(t)
    if model is None or model["summary"].get("rating") != "Outperform":
        return   # prepare_demo_portfolio hasn't run (tests, or no price feed)

    def quill_acts(params: dict) -> TurnResult:
        board = _last_tool_json(params)
        held = any(p["ticker"] == t for p in office.store.positions("open"))
        waiting = any(c["kind"] == "portfolio" and c["payload"].get("ticker") == t
                      for c in office.store.approvals("pending"))
        if held or waiting:
            line = board.get("summary", "") if held else f"The {t} entry is still waiting for your decision."
            return think("Nothing new to propose. The scoreboard speaks for itself.", None,
                         ("report_to_captain", {"text": f"Portfolio check (demo): {line}"}))(params)
        return think(f"{t} has an approved model rated Outperform, and we don't hold it. A middle "
                     "conviction size until the next earnings report.", None,
                     ("propose_position", {"ticker": t, "size_pct": 5,
                                           "thesis": "Demo entry on the real rules: the approved model "
                                                     "rates it Outperform. Middle size (5%) until the next "
                                                     "earnings report confirms the thesis; a miss on growth "
                                                     "would prove it wrong."}))(params)

    llm.script("er_lead",
               think("The portfolio first: what do we hold, and how are we doing against the S&P 500?",
                     None, ("read_portfolio", {})),
               quill_acts,
               think("Done.", "Portfolio reviewed."))
    office.assign("Quill", "Review the paper portfolio and propose an entry if one qualifies (demo).",
                  title="Portfolio review (demo)")


DRY_RUN_TICKER = "META"   # real coverage in the erb submodule: the dry run uses real tools on it


def _last_tool_json(params: dict) -> dict:
    """The JSON a tool returned in the previous turn (the demo reads real numbers from it)."""
    for block in reversed(params["messages"][-1]["content"]):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            try:
                return json.loads(block["content"])
            except (TypeError, ValueError):
                return {}
    return {}


def scene_quant_model(office: Office, llm: DemoLLM) -> None:
    """Free dry run of the Research -> Quant -> Captain chain with REAL tools on META: the
    workbook, formula check, simulations, registry and approval card are all real; only the
    agents' words are scripted."""
    t = DRY_RUN_TICKER
    if not (coverage_dir(t) / "assumptions.yaml").exists():
        return   # no local coverage (e.g. a fresh clone): skip the dry run
    llm.script("er_lead",
               think("Our thesis and assumptions for META are in coverage/META. Quant owns the "
                     "model, so I hand the assumptions over instead of building a valuation.", None,
                     ("send_message", {"to": ["Sigma"], "text": f"{t} assumptions are ready in "
                                       f"coverage/{t} (demo dry run, real tools). Please build the "
                                       "model."})),
               think("Handed off.", "Assumptions sent to Quant."))
    llm.script("quant_lead",
               think("DCF with a CAPM cost of capital, the multiples blend, bull/base/bear, then a "
                     "Monte Carlo. Delta builds and checks; I review and sign off.", None,
                     ("delegate", {"to": "Delta", "job": f"Build the {t} model with build_model, "
                                   "then run_simulations. Return the version, price targets, the "
                                   "formula-check result and the top value drivers."})),
               lambda p: _sigma_files_for_approval(p, t),
               think("Quill needs to know it's with the Captain.", None,
                     ("send_message", {"to": ["Quill"], "text": f"{t} model is with Stott for "
                                       "approval. Hold any figures until it's approved."})),
               think("Done.", "Model sent for approval."))
    llm.script("quant_associate",
               think("Building the workbook from assumptions.yaml, then the formula check.", None,
                     ("build_model", {"ticker": t})),
               think("Built and checked. Now the Monte Carlo and the value drivers.", None,
                     ("run_simulations", {"ticker": t, "runs": 1000})),
               lambda p: _delta_reports(p, t))
    office.assign("Quill", f"Hand the {t} assumptions to Quant for the model (demo dry run with "
                  "real tools).", title=f"{t} to Quant (demo, real tools)")


def _delta_reports(params: dict, t: str) -> TurnResult:
    sims = _last_tool_json(params)
    built = {}
    for m in params["messages"]:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result" and '"check"' in str(b.get("content")):
                    try:
                        built = json.loads(b["content"])
                    except ValueError:
                        pass
    pts = built.get("price_targets", {})
    drivers = ", ".join(d["driver"] for d in sims.get("top_value_drivers", [])[:3])
    p10, p50, p90 = sims.get("price_target_p10_p50_p90", [0, 0, 0])
    findings = (f"{t} model v{built.get('version')} built: bear ${pts.get('bear', 0):,.2f}, base "
                f"${pts.get('base', 0):,.2f}, bull ${pts.get('bull', 0):,.2f} ({built.get('rating')}). "
                f"{built.get('check', '')} Monte Carlo P10/P50/P90 ${p10:,.0f}/${p50:,.0f}/${p90:,.0f}. "
                f"Top drivers: {drivers}.")
    return think("Reporting the build, check and simulations.", None,
                 ("submit_result", {"findings": findings, "figures": [
                     {"label": "Base price target", "value": pts.get("base", 0), "unit": "USD",
                      "source": f"Quant model v{built.get('version')} (workbook Outputs)"}],
                  "open_questions": [], "confidence": "high"}))(params)


def _sigma_files_for_approval(params: dict, t: str) -> TurnResult:
    result = _last_tool_json(params)
    text = result.get("findings", "")
    m = re.search(r"model v(\d+)", text)
    version = int(m.group(1)) if m else 1
    return think("The build checks out against the engine. Filing it for the Captain's approval "
                 "with the numbers attached.", None,
                 ("request_approval", {"kind": "model", "ticker": t, "version": version,
                                       "title": f"{t} model v{version} (demo dry run)",
                                       "summary": text or f"{t} model v{version} is ready.",
                                       "attachments": [f"quant/{t}_model_v{version}.xlsx"]}))(params)


DEMO_PENDING_KEEP = 3

SCENES = [scene_gem_hunt, scene_memo, scene_quant_model, scene_audit, scene_portfolio,
          scene_lobby_sync, scene_newsletter]


async def run_demo(office: Office, llm: DemoLLM, pause: float = 6.0) -> None:
    """Loop the scenes forever, one at a time, with a breather between them."""
    for scene in itertools.cycle(SCENES):
        # Scenes repeat word for word; without a fresh guard the near-duplicate check would
        # (correctly) block every scripted message from the second loop on.
        office.conversations = ConversationGuard()
        llm.scripts.clear()   # leftovers from an interrupted scene must not play later
        # Nobody decides the demo's cards, so keep only the newest few instead of a pile.
        pending = office.store.approvals("pending")
        import shutil
        for card in pending[:-DEMO_PENDING_KEEP]:
            office.store.decide_approval(card["id"], "expired", "Tidied up by the demo")
            office._outbox_decided(card, "expired")   # its Outbox entry stops waiting too
        folders = [f for f in (office.outbox_dir / "newsletters").glob("*") if f.is_dir()]
        for stale in sorted(folders, key=lambda f: f.stat().st_mtime)[:-DEMO_PENDING_KEEP]:
            shutil.rmtree(stale, ignore_errors=True)   # the demo's own Outbox folder only
        for held in office.store.memory_writes(status="pending")[:-DEMO_PENDING_KEEP]:
            office.store.set_memory_status(held["id"], "expired")
        usable = office.ledger.daily_cap - office.ledger.audit_reserve
        if office.ledger.spent_today() > usable * 0.5:
            office.store.clear_spend()   # demo spend is pretend; keep the meter in range
        if scene is scene_portfolio:
            await prepare_demo_portfolio(office)
        scene(office, llm)
        await asyncio.sleep(1)
        await office.idle()
        if scene is scene_audit:   # normally posted once a finished day; shown here for the tour
            office.post_digest(office.ledger.today())
        await asyncio.sleep(pause)
