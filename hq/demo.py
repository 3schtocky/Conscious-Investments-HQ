"""Demo mode: the real office engine driven by a scripted model, at zero API cost.

`hq serve --demo` uses this so the office UI can be toured and tuned for free. Everything
downstream is authentic (tasks, guards, delegation, chat, spend accounting); only the model's
words are scripted. Companies in the scenes are fictional and marked (demo).
"""

from __future__ import annotations

import asyncio
import itertools
import random
import re
from collections import defaultdict, deque
from collections.abc import Callable

from hq.engine.guards import ConversationGuard
from hq.engine.llm import TurnResult
from hq.engine.runtime import Office

_ids = itertools.count(1)
_WHO = re.compile(r"You are \*\*.+?\*\* \(`(\w+)`\)")


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


class DemoLLM:
    """Streams scripted turns word by word, so the office looks alive."""

    def __init__(self, speed: float = 1.0):
        self.scripts: dict[str, deque] = defaultdict(deque)
        self.speed = speed

    def script(self, agent_id: str, *turns: Callable) -> None:
        self.scripts[agent_id].extend(turns)

    async def turn(self, *, params, on_delta=None, on_block=None) -> TurnResult:
        m = _WHO.search(params["system"])
        who = m.group(1) if m else "?"
        await asyncio.sleep(0.6 / self.speed)   # time to first token
        if self.scripts[who]:
            result = self.scripts[who].popleft()(params)
        else:
            result = think("Nothing left on my list for this one.", "All set on my end.")(params)
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
    llm.script("screen_lead",
               think("The Captain wants small-cap gems. I'll have Pip run the Gems screen and dig "
                     "catalysts while I think about which themes deserve conviction. Energy "
                     "storage and power semis both have real demand tailwinds.",
                     "Kicking off the gem hunt.",
                     ("delegate", {"to": "Pip", "job": "Run the Gems screen (demo). Return the top "
                                   "two names with one catalyst each and a source per figure."})),
               think("Pip's two names look interesting. Northwind Storage has a contract catalyst "
                     "and Lumen Power has margin expansion. Quill should see these for a memo.",
                     None,
                     ("send_message", {"to": ["Quill"], "text": "Two gems for a memo: Northwind "
                                       "Storage (demo) and Lumen Power (demo). Pip's catalysts "
                                       "are in the screen folder."})),
               think("Handoff done. I'll brief the Captain.", None,
                     ("report_to_captain", {"text": "Gem hunt (demo): two candidates, Northwind "
                                            "Storage and Lumen Power. Both passed sanity checks. "
                                            "Sent to Quill for memos."})),
               think("Done.", "Shortlist sent to Quill and the Captain."))
    llm.script("screen_associate",
               think("Running the Gems preset: $0.3 to 15bn market cap, revenue acceleration, "
                     "gross margin expansion, momentum. Then catalyst digging on the top names.",
                     None,
                     ("submit_result", {"findings": "1. Northwind Storage (demo): utility contract "
                                        "pipeline.\n2. Lumen Power (demo): gross margin up four "
                                        "quarters running.",
                                        "figures": [],
                                        "open_questions": ["Contract timing unconfirmed (demo)"],
                                        "confidence": "medium"})))
    llm.script("er_lead",
               think("Scout sent two names. I'll acknowledge and queue memos after the current "
                     "work.", None,
                     ("send_message", {"to": ["Scout"], "text": "Got them. I'll start with "
                                       "Northwind."})),
               think("Acknowledged.", "Queued two memos."))
    office.assign("Scout", "Find two small-cap gems in energy storage or power semis (demo).",
                  title="Gem hunt (demo)")


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
                     ("report_to_captain", {"text": "Northwind Storage memo (demo) drafted. "
                                            "Thesis: contract pipeline converts in 12 to 18 "
                                            "months. Audit is spot-checking sources."})),
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


def scene_newsletter(office: Office, llm: DemoLLM) -> None:
    llm.script("cr_lead",
               think("This week's newsletter: lead with the gem hunt, then the memo pipeline. "
                     "Wren drafts, I finalize and run lint and the disclosure check.", None,
                     ("delegate", {"to": "Wren", "job": "Draft this week's newsletter (demo): gem "
                                   "hunt recap and memo pipeline, 400 words, plus two social "
                                   "teasers."})),
               think("Wren's draft reads well. Disclosure added. Ready for the Captain's "
                     "approval.", None,
                     ("report_to_captain", {"text": "Newsletter draft (demo) is ready for your "
                                            "approval in the Outbox."})),
               think("Done.", "Newsletter ready for approval."))
    llm.script("cr_associate",
               think("Plain, human tone. No hype. Hook with the gem hunt.", None,
                     ("submit_result", {"findings": "Draft: 'Two small caps worth a closer look "
                                        "this week...' (demo). Two teasers written.",
                                        "figures": [], "open_questions": [],
                                        "confidence": "high"})))
    office.assign("Harbor", "Prepare this week's newsletter (demo).", title="Newsletter (demo)")


SCENES = [scene_gem_hunt, scene_memo, scene_lobby_sync, scene_newsletter]


async def run_demo(office: Office, llm: DemoLLM, pause: float = 6.0) -> None:
    """Loop the scenes forever, one at a time, with a breather between them."""
    for scene in itertools.cycle(SCENES):
        # Scenes repeat word for word; without a fresh guard the near-duplicate check would
        # (correctly) block every scripted message from the second loop on.
        office.conversations = ConversationGuard()
        llm.scripts.clear()   # leftovers from an interrupted scene must not play later
        usable = office.ledger.daily_cap - office.ledger.audit_reserve
        if office.ledger.spent_today() > usable * 0.5:
            office.store.clear_spend()   # demo spend is pretend; keep the meter in range
        scene(office, llm)
        await asyncio.sleep(1)
        await office.idle()
        await asyncio.sleep(pause)
