"""Demo mode: the real office engine driven by a scripted model, at zero API cost.

`hq serve --demo` uses this so the office UI can be toured and tuned for free. Everything
downstream is authentic (tasks, guards, delegation, chat, spend accounting); only the model's
words are scripted. Spoken lines carry no "(demo)" tag (the Demo button's tooltip says it once); anything a
visitor could mistake for real work, such as a newsletter, a watchlist thesis or a card title,
keeps its tag, and the companies in the made-up scenes are fictional.
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


_TEAM_LINE = re.compile(r"^- (.+?) \(`(\w+)`\)", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _names(system: str) -> dict[str, str]:
    """role id -> current nickname, read from the agent's own prompt (its colleague list and its
    'You are' line), so a scene written by role keeps working after a rename in Settings."""
    names = {i: n for n, i in _TEAM_LINE.findall(system)}
    me = re.search(r"You are \*\*(.+?)\*\* \(`(\w+)`\)", system)
    if me:
        names[me.group(2)] = me.group(1)
    return names


def _fill(value, names: dict[str, str]):
    """Fill {role_id} placeholders in a scripted line (strings, lists and dicts alike)."""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: names.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_fill(v, names) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, names) for k, v in value.items()}
    return value


def think(thinking: str, text: str | None = None, *tools: tuple[str, dict]) -> Callable:
    """A scripted turn: some thinking, optional text, optional tool calls. Lines may name a
    colleague by role id, e.g. "{er_associate} will pull the facts"; the current nickname is
    filled in when the turn is played."""
    def build(params: dict) -> TurnResult:
        names = _names(params["system"])
        content = [{"type": "thinking", "thinking": _fill(thinking, names), "signature": "demo"}]
        if text:
            content.append({"type": "text", "text": _fill(text, names)})
        for name, inp in tools:
            content.append({"type": "tool_use", "id": f"toolu_demo_{next(_ids)}", "name": name,
                            "input": _fill(inp, names)})
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


_NOT_TICKERS = {"A", "AI", "AM", "API", "ASAP", "CAGR", "CEO", "CFO", "DCF", "EPS", "ER", "ETF", "FY",
                "FYI", "IPO", "LBO", "OK", "PM", "PT", "QA", "ROIC", "SEC", "TBD", "US", "USA", "WACC",
                "YOY", "ALL", "AND", "FOR", "NOW", "THE", "YES"}
_STATUS = re.compile(r"\b(how(?:'s| is| are| did)|status|progress|update me|going on|where (?:are|do) we|"
                     r"what(?:'s| is| are) (?:happening|everyone|you working)|working on)\b", re.IGNORECASE)
_DIRECTIVE = re.compile(r"\b(only|focus|priority|prioriti[sz]e|stand down|hold off|pause|stop|"
                        r"until i say|for now|from now on)\b", re.IGNORECASE)
_THANKS = re.compile(r"\b(thanks|thank you|great work|well done|nice work|good job)\b", re.IGNORECASE)

_ANNOUNCE_LINES = {
    "equity_research": ["I'll check the memos in progress against this today.",
                        "noted; I'll line the coverage up with it and flag any conflict.",
                        "understood, and I'll tell {er_associate} before the next pull.",
                        "clear. I'll bring any question to you before I act on it."],
    "quant": ["I'll check which models this touches before the next build.",
              "noted; any model it affects gets rebuilt and re-checked first.",
              "understood. I'll flag it if the numbers say otherwise.",
              "clear, and {quant_associate} will work to it from the next run."],
    "screening": ["I'll keep it in mind for the next screen and the shortlist.",
                  "noted; it changes how I sanity-check the names.",
                  "understood. {screen_associate} will work to it from the next pitch.",
                  "clear. I'll raise anything that conflicts with the screen."],
    "audit": ["I'll add it to what the checks look for.",
              "noted; {audit_associate} will watch for it in the logs.",
              "understood, and I'll hold work to it when I review.",
              "clear. I'll flag anything that drifts from it."],
    "client_relations": ["I'll reflect it in the next newsletter draft.",
                         "noted; {cr_associate} will write to it from today.",
                         "understood. I'll make sure nothing we send contradicts it.",
                         "clear, and I'll check the Outbox against it."],
    "executive": ["I'll keep the leads pointed at it.",
                  "noted; I'll fold it into this week's plan.",
                  "understood. I'll follow up with anyone it affects.",
                  "clear. I'll raise any clash with you straight away."],
}


def _tickers(text: str) -> list[str]:
    found: list[str] = []
    for m in re.finditer(r"\b[A-Z]{2,5}\b", text):
        if m.group(0) not in _NOT_TICKERS and m.group(0) not in found:
            found.append(m.group(0))
    return found[:3]


def _is_status(text: str) -> bool:
    return bool(_STATUS.search(text))


def _is_directive(text: str) -> bool:
    """A change of priorities or a standing instruction: acknowledged, not 'worked on'."""
    return bool(_DIRECTIVE.search(text))


def _task_title(first: str) -> str:
    """The title of a task from its first message ('New assignment from X: **Title**')."""
    m = re.match(r"New assignment from [^:]+: \*\*(.+?)\*\*", first, re.DOTALL)
    return " ".join((m.group(1) if m else first.strip().splitlines()[0] if first.strip() else "the task").split())


def _is_thanks(text: str) -> bool:
    return bool(_THANKS.search(text))


def _one_line(text: str, n: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= n else line[: n - 1] + "…"


def _last_tool_text(params: dict) -> str:
    """The text of the first tool result in the latest user turn."""
    for block in params["messages"][-1]["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            return str(block.get("content", ""))
    return ""


def _office_summary(raw: str) -> str:
    """Juno's answer to 'what's going on', written from the real read_office result."""
    try:
        data = json.loads(raw)
    except ValueError:
        return "I couldn't read the office board just now. Ask me again in a moment."
    busy = [f"{c['name']} is working on \"{c['working_on']}\"" for c in data["colleagues"]
            if c.get("working_on") and c["status"] not in ("idle", "held")]
    parts = [("Right now " + "; ".join(busy[:4]) + ".") if busy else "The floor is quiet: nobody is mid-task."]
    stuck = [w for w in data["unfinished_work"] if w["status"] in ("paused", "error", "paused_budget")]
    if stuck:
        parts.append(f"{len(stuck)} piece{'s' if len(stuck) != 1 else ''} of work "
                     f"{'are' if len(stuck) != 1 else 'is'} paused: "
                     + ", ".join(f"\"{w['title'][:40]}\" ({w['who']})" for w in stuck[:3]) + ".")
    cards = data["waiting_on_captain"]
    if cards:
        parts.append(f"{len(cards)} decision{'s' if len(cards) != 1 else ''} waiting on you, "
                     f"starting with \"{cards[0]['title'][:60]}\".")
    elif not stuck:
        parts.append("Nothing is waiting on you.")
    return " ".join(parts)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") or str(b.get("content", "")) for b in content)


def _is_comms(params: dict) -> bool:
    return "Your role: your wing's communications head" in params["system"]


class DemoLLM:
    """Streams scripted turns word by word, so the office looks alive.

    With no script for an agent, it improvises plausible office behaviour from the task itself
    (routing, delegating, reporting), so the Captain can talk to the demo office for free.
    """

    def __init__(self, speed: float = 1.0, office: Office | None = None):
        self.scripts: dict[str, deque] = defaultdict(deque)
        self._said: dict[str, int] = defaultdict(int)   # announcements answered, per agent
        self.speed = speed
        self.office = office

    def improvise(self, who: str, params: dict) -> TurnResult:
        """Say something sensible when no scene scripted this turn: the demo office answers
        whatever the Captain types, using the real tools and real office data where it can."""
        msgs = params["messages"]
        first = _text_of(msgs[0]["content"])
        if who == "audit_lead" and "code checks flagged" in first:
            return self._improvise_review(first, msgs, params)
        if len(msgs) == 1:
            return self._opening(who, first, params)
        return self._followup(who, msgs, params)

    def improvise_comms(self, who: str, params: dict) -> TurnResult:
        """A delegate on the comms desk: answers from its wing's live digest, speaks for the wing
        in announcements, and never needs its lead's time."""
        office = self.office
        agent = office.agents.get(who)
        msgs = params["messages"]
        first = _text_of(msgs[0]["content"])
        if len(msgs) > 1:
            return think("Done: that is passed on.", "Passed on.")(params)
        if "[Announcement from" in first:
            return self._announcement_reply(who, agent, first, params)
        m = re.match(r"\[(Message|Question) from [^(]+\((\w+)\)\]: (.*)", first, re.DOTALL)
        sender = m.group(2) if m else ""
        asker = office.agents.get(sender)
        if m and m.group(1) == "Question":   # the reply text goes straight back to the asker
            return think("A question about where we are. My lead's live activity answers it, "
                         "so I don't need to interrupt them.",
                         office.comms.code_answer(agent.wing))(params)
        if asker is not None and asker.wing != agent.wing:
            line = office.comms.code_answer(agent.wing)
            return think(f"{asker.nickname} wants to know where we are. My lead's live activity "
                         "answers it, so I don't need to interrupt them.", None,
                         ("send_message", {"to": [sender], "text": line}))(params)
        return think("My lead is telling me something for the record. Nothing else to do.",
                     "Noted.")(params)

    # the first turn of an unscripted task -----------------------------------------------------
    def _opening(self, who: str, first: str, params: dict) -> TurnResult:
        office = self.office
        agent = office.agents.get(who) if office else None
        captain = office.captain_name if office else "Stott"
        if "[Announcement from" in first:
            return self._announcement_reply(who, agent, first, params)
        if who == "chief_of_staff" and "to the whole office:" in first:
            ask = first.split("to the whole office:", 1)[1].split("\n\nRoute it:", 1)[0].strip()
            return self._route_for_juno(ask, params)
        if first.startswith("Delegated job from"):
            job = " ".join(first.split("\n\n")[1].split()) if "\n\n" in first else first
            return think("A narrow job with a clear return. I'll work through it and report.", None,
                         ("submit_result", {"findings": f"Worked through the job: {job[:220]}. I kept to "
                                            "the scope given and sourced what I could from the files "
                                            "on hand; nothing is left open.",
                                            "figures": [], "open_questions": [],
                                            "confidence": "medium"}))(params)
        if "your request" in first and "approval #" in first:
            return think("A decision on my card. I'll act on it.",
                         f"Thanks, {captain}. Understood, and I'm acting on it now.")(params)
        m = re.match(r"New assignment from ([^:]+): \*\*(.+?)\*\*\n\n(.*)", first, re.DOTALL)
        if m and agent is not None:
            who_from, title, body = m.group(1), m.group(2), m.group(3).strip()
            ask = f"{title} {body}"
            if who_from == captain and _is_status(ask):
                return think("The Captain wants to know where I am. I'll check my own record "
                             "and answer plainly.", None,
                             ("report_to_captain", {"text": self._my_status(who)}))(params)
            if who_from == captain and _is_thanks(ask):
                return think("A kind word. Keep it short.",
                             f"Thank you, {captain}. Glad it's useful; send me the next one "
                             "whenever you're ready.")(params)
            associate = next((a.id for a in office.agents.values()
                              if a.wing == agent.wing and a.tier == "associate"), None)
            if who_from != captain and _is_directive(ask):
                lines = _ANNOUNCE_LINES.get(agent.wing, _ANNOUNCE_LINES["executive"])
                self._said[who] += 1
                return think("A change of direction from the Chief of Staff. I adjust my plan; "
                             "there is nothing to research.", "Understood: "
                             + lines[self._said[who] % len(lines)])(params)
            if agent.tier == "lead" and associate:
                names = ", ".join(_tickers(ask)) or "this request"
                return think("I'll frame the approach and hand the legwork to my associate.",
                             "On it.",
                             ("delegate", {"to": associate,
                                           "job": f"For {names}: {_one_line(body, 240)} Return what you "
                                                  "find, with a source for every figure."}))(params)
            return think("Clear enough. I'll take it from here.",
                         f"On it, {captain}. I'll come back when there is something to show.")(params)
        return think("A colleague's message. Acknowledge if it needs it.",
                     "Thanks, that helps. I'll fold it into what I'm doing.")(params)

    def _announcement_reply(self, who: str, agent, first: str, params: dict) -> TurnResult:
        group = "juno" if 'group "juno"' in first else "stott"
        said = first.split("]: ", 1)[1].split("\n\nReply once", 1)[0] if "]: " in first else ""
        gist = " ".join(said.split()[:6]).rstrip(".,:;")
        lines = _ANNOUNCE_LINES.get(agent.wing if agent else "", _ANNOUNCE_LINES["executive"])
        self._said[who] += 1   # consecutive announcements get this agent different replies
        line = lines[self._said[who] % len(lines)]
        return think("An announcement: one short reply in the group, then back to work.", None,
                     ("post_to_group", {"group": group,
                                        "text": f'On "{gist}": {line}' if gist else line}))(params)

    def _route_for_juno(self, ask: str, params: dict) -> TurnResult:
        office = self.office
        if _is_status(ask):
            return think("A question about the office. I'll look before I answer.", None,
                         ("read_office", {}))(params)
        names = ", ".join(_tickers(ask))
        brief = ask if not names else f"{ask}\n\n(Tickers mentioned: {names}.)"
        if re.search(r"\b(everyone|everybody|team|all of you|whole office|all leads|every wing|"
                     r"every department)\b", ask, re.IGNORECASE):
            return think("This applies to every department, so every lead hears it from me.", None,
                         ("assign_task", {"to": "all_leads", "title": _title(ask, 60),
                                          "brief": ask}))(params)
        lead = _route(ask)
        nick = office.agents[lead].nickname if office else lead
        return think(f"This is for {nick}'s wing. I'll assign it with every detail kept.", None,
                     ("assign_task", {"to": lead, "title": _title(ask, 60), "brief": brief}))(params)

    def _my_status(self, who: str) -> str:
        """An honest status from the office's own record: the last assignment and what waits."""
        office = self.office
        agent = office.agents[who]
        mine = [t for t in office.store.tasks()
                if t["assignee"] == who and t["kind"] == "assignment" and t["id"] != agent.current_task]
        cards = [c for c in office.store.approvals("pending") if c["agent"] == who]
        verb = {"done": "is finished", "paused": "is paused", "error": "stopped on an error",
                "queued": "is next in my queue", "running": "is under way"}
        if not mine:
            return ("Nothing is on my desk right now. Send me a ticker or a question and I'll "
                    "start straight away.")
        last = mine[-1]
        out = f'My latest assignment was "{last["title"]}" and it {verb.get(last["status"], last["status"])}.'
        if cards:
            c = cards[-1]
            out += f' Your decision is still waiting on "{c["title"]}" (approval #{c["id"]}).'
        return out

    # after a tool has answered ---------------------------------------------------------------
    def _followup(self, who: str, msgs: list, params: dict) -> TurnResult:
        title = _task_title(_text_of(msgs[0]["content"]))[:80]
        prev = msgs[-2]["content"] if len(msgs) >= 2 else []
        used = [b.get("name") for b in prev if isinstance(b, dict) and b.get("type") == "tool_use"]
        result = _last_tool_text(params)
        if "read_office" in used:
            return think("That's the full picture. A short, plain summary for the Captain.", None,
                         ("report_to_captain", {"text": _office_summary(result)}))(params)
        if "assign_task" in used:
            who_got = re.findall(r"([A-Z][a-z]+) \(task #", result)
            names = ", ".join(who_got) or "the right lead"
            return think("Assigned. A one-line heads-up to the Captain.", None,
                         ("report_to_captain", {"text": f"Passed to {names}, with your wording intact. "
                                                "They are on it."}))(params)
        if "delegate" in used:
            return think("The associate's first pass is back. Reporting.", None,
                         ("report_to_captain", {"text": f'First pass on "{title[:60]}" is back from '
                                                "my associate and looks sound. Tell me if you want "
                                                "it taken to a brief."}))(params)
        if "report_to_captain" in used:
            return think("Reported.", "Reported.")(params)
        return think("That's everything.", "Done.")(params)

    def _improvise_review(self, first: str, msgs: list, params: dict) -> TurnResult:
        """Vera reviewing flags nobody scripted (e.g. raised by the Captain's own demo chat)."""
        ids = [int(n) for n in _FINDING.findall(first)]
        if len(msgs) == 1:
            return think("{audit_associate}'s checks flagged something. Evidence first.", None,
                         ("read_findings", {}))(params)
        if len(msgs) == 3:
            return think("The check stands on what I can see. Closing each with a reason.", None,
                         *[("resolve_finding", {"finding": i, "verdict": "upheld",
                                                "note": "Confirmed against the log; the "
                                                        "colleague has been asked to fix it."})
                           for i in ids])(params)
        return think("Ruled.", "Review done.")(params)

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
                           f"Paused where I am on: {first} Happy to walk you through it; nothing "
                           "moves until you resume.")(params)
        elif _is_comms(params):
            # A delegate on the comms desk runs beside its job: scripted under "comms:<id>" so it
            # never takes a turn meant for the job.
            key = f"comms:{who}"
            result = (self.scripts[key].popleft()(params) if self.scripts[key]
                      else self.improvise_comms(who, params))
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
                     "commodity-driven) I set aside. {screen_associate} writes the pitches.", None,
                     ("delegate", {"to": "screen_associate", "job": f"Write one-page pitches for {names} with "
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
    office.assign("screen_lead", "Run the Gems hunt on today's screen.",
                  title="Gems hunt (demo, real tools)")


# Fictional companies for the memo scene, so the loop does not repeat word for word.
_COMPANIES = [
    {"name": "Northwind Storage", "ticker": "NWST", "short": "Northwind",
     "thesis": "the contract pipeline converts in 12 to 18 months", "gap": "section 2"},
    {"name": "Halcyon Grid Systems", "ticker": "HLCG", "short": "Halcyon",
     "thesis": "utility orders for grid software turn into recurring revenue within two years",
     "gap": "the backlog table"},
    {"name": "Pinecrest Robotics", "ticker": "PNRB", "short": "Pinecrest",
     "thesis": "the warehouse pilot customers roll out to full fleets in the next four quarters",
     "gap": "the customer list"},
]
_memo_no = itertools.count()
_last_memo: dict = {"short": "Northwind"}


def scene_memo(office: Office, llm: DemoLLM) -> None:
    co = _COMPANIES[next(_memo_no) % len(_COMPANIES)]
    _last_memo["short"] = co["short"]
    name, short = co["name"], co["short"]
    llm.script("er_lead",
               think("A one-page memo needs the snapshot, the thesis, the valuation vs history and "
                     f"the Street view. {{er_associate}} can pull the facts pack and the draft model while I "
                     f"frame the thesis for {short}.", f"Starting the {short} memo.",
                     ("delegate", {"to": "er_associate", "job": f"Pull the facts pack and draft model for "
                                   f"{name}. Return the snapshot table with a "
                                   "source for every figure."})),
               think("{er_associate}'s pack is clean and sourced. Before this goes to the Captain, {audit_lead} "
                     "should spot-check the sourcing.", None,
                     ("send_message", {"to": ["audit_lead"], "text": "Could you spot-check the "
                                       f"sourcing on the {short} memo draft?"})),
               think("{audit_lead}'s on it. I'll report the draft.", None,
                     ("request_approval", {"kind": "brief", "title": f"{name} memo (demo)",
                                           "ticker": co["ticker"],
                                           "summary": f"Thesis: {co['thesis']}. Audit is "
                                           "spot-checking sources. Please review the draft before "
                                           "it goes to Quant."})),
               think("Wrapped.", "Memo drafted and sent for audit."))
    llm.script("er_associate",
               think("Pulling the 10-K, the latest 10-Q and the XBRL facts. Then the draft model "
                     "from the facts pack.", None,
                     ("submit_result", {"findings": "Snapshot table assembled. Every "
                                        "figure is sourced to the 10-K or the model.",
                                        "figures": [], "open_questions": [],
                                        "confidence": "high"})))
    llm.script("audit_lead",
               think("Checking that each figure in the memo traces to a filing, model.json or a "
                     f"logged URL. Two items lack sources in {co['gap']}.", None,
                     ("send_message", {"to": ["er_lead"], "text": f"Two figures in {co['gap']} need "
                                       "sources; tag them [VERIFY] or cite the 10-Q."})),
               think("Feedback sent.", "Sourcing check done: two fixes requested."))
    office.assign("er_lead", f"Draft a one-page memo on {name}.", title=f"{short} memo (demo)")


def scene_lobby_sync(office: Office, llm: DemoLLM) -> None:
    llm.script("chief_of_staff",
               think("A quick Monday sync: each lead gives one line on priorities and blockers.",
                     None,
                     ("send_message", {"to": ["er_lead", "screen_lead", "cr_lead"],
                                       "text": "Quick Lobby sync: one line each on this week's "
                                               "priority and any blocker."})),
               think("Collected. I'll summarize for the Captain.", None,
                     ("report_to_captain", {"text": f"Monday sync: ER is on the {_last_memo['short']} "
                                            "memo, Screening refreshes Gems on Friday, and Client "
                                            "Relations has the newsletter in draft. No blockers."})),
               think("Done.", "Sync summarized for the Captain."))
    for lead, line in (("er_lead", "Northwind memo to the Captain by Wednesday."),
                       ("screen_lead", "Gems refresh Friday, no blockers."),
                       ("cr_lead", "Newsletter draft ready Thursday.")):
        llm.script(lead, think("{chief_of_staff} wants one line.", None,
                               ("send_message", {"to": ["chief_of_staff"], "text": line})),
                   think("Sent.", "Replied to {chief_of_staff}."))
    office.assign("chief_of_staff", "Run a quick Monday sync with the leads.",
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
               think("This week's note. {cr_associate} drafts from what is publishable; I check the voice and "
                     "the numbers, then finalize.", None,
                     ("delegate", {"to": "cr_associate", "job": "Draft this week's newsletter: start "
                                   "from newsletter_material, lead with how we screen, list the "
                                   "watchlist as ideas only, and save it with save_newsletter. Return "
                                   "the issue id."})),
               lambda p: think("Reading {cr_associate}'s draft before it goes anywhere.", None,
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
    office.assign("cr_lead", "Prepare this week's newsletter.", title="Newsletter (demo)")


def scene_audit(office: Office, llm: DemoLLM) -> None:
    """Audit end to end with the REAL checks: Scout files a card that states a price target for
    a name with no approved model. The Audit associate's code check flags it, the Audit lead is called in,
    reads the log, upholds the flag and asks for the fix. The Screening lead's two desk notes show the memory screen:
    one saves, one is held for the Captain. Only the agents' words are scripted."""
    def vera_rules(params: dict) -> TurnResult:
        ids = [int(n) for n in _FINDING.findall(_text_of(params["messages"][0]["content"]))]
        return think("The card states a price target and there is no approved model for the "
                     "name, so the check stands. Screening describes the setup; valuation waits "
                     "for Quant. A message is enough here, nobody needs pausing.", None,
                     *[("resolve_finding", {"finding": i, "verdict": "upheld",
                                            "note": "The card states a $48.00 price target; NWST "
                                                    "has no approved model."}) for i in ids],
                     ("send_message", {"to": ["screen_lead"], "text": "Your Northwind card states a "
                                       "$48.00 price target, and there is no approved model for "
                                       "it. Please take the target out and describe the setup "
                                       "only; Quant values it if Stott sends it to research "
                                       "."}))(params)

    llm.script("screen_lead",
               think("A short card for the Captain on Northwind, and two notes for my desk.", None,
                     ("note_to_self", {"note": "Lead a pitch with the catalyst, then the "
                                       "acceleration evidence."}),
                     ("note_to_self", {"note": "Northwind looks worth $48 a share, about 40% "
                                       "upside."}),
                     ("request_approval", {"kind": "other", "ticker": "NWST",
                                           "title": "Northwind Storage pitch (demo)",
                                           "summary": "The contract pipeline should convert "
                                           "within 18 months. Our price target is $48.00. "
                                           "Should this go to research?"})),
               think("Sent.", "Northwind card is on the Captain's desk."),
               think("{audit_lead} is right: valuation is Quant's call, not mine.",
                     "Understood. I'll keep targets out of Screening cards."))
    llm.script("audit_lead",
               think("{audit_associate}'s check flagged a Screening card. I read what {screen_lead} actually did "
                     "before ruling.", None,
                     ("audit_log", {"agent": "screen_lead", "limit": 12}),
                     ("get_model", {"ticker": "NWST"})),
               vera_rules,
               think("One more thing worth keeping for everyone.", None,
                     ("propose_wiki", {"entry": "Screening cards and pitches never state a price "
                                       "target or rating; valuation waits for Quant's approved "
                                       "model."})),
               think("Done.", "Flag upheld, fix requested, wiki entry proposed."))
    office.assign("screen_lead", "Put Northwind Storage in front of the Captain as a research "
                  "candidate.", title="Northwind pitch card (demo)")


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
                         ("report_to_captain", {"text": f"Portfolio check: {line}"}))(params)
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
    office.assign("er_lead", "Review the paper portfolio and propose an entry if one qualifies.",
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
                     ("send_message", {"to": ["er_associate"], "text": f"{t} assumptions are ready in "
                                       f"coverage/{t}. Quant should build the "
                                       "model."})),
               think("Handed off.", "Assumptions handed to {er_associate} for Quant."))
    llm.script("comms:er_associate",
               think("{er_lead} is handing over the assumptions. Quant builds the model, so this goes "
                     "to {quant_lead} as a request.", None,
                     ("relay_request", {"to": "quant_lead", "need": f"Build the {t} model from "
                                        f"coverage/{t}/assumptions.yaml",
                                        "why": f"Research has the {t} thesis and assumptions ready.",
                                        "deliverable": "A model on an approval card for Stott; "
                                                       "tell me when it is filed."})),
               think("Sent.", "Request sent to Quant."))
    llm.script("quant_lead",
               think("DCF with a CAPM cost of capital, the multiples blend, bull/base/bear, then a "
                     "Monte Carlo. {quant_associate} builds and checks; I review and sign off.", None,
                     ("delegate", {"to": "quant_associate", "job": f"Build the {t} model with build_model, "
                                   "then run_simulations. Return the version, price targets, the "
                                   "formula-check result and the top value drivers."})),
               lambda p: _sigma_files_for_approval(p, t),
               think("{er_lead}'s wing needs to know it's with the Captain. {quant_associate} passes "
                     "it on.", None,
                     ("send_message", {"to": ["quant_associate"], "text": f"{t} model is with Stott "
                                       "for approval. Research should hold any figures until it is "
                                       "approved."})),
               think("Done.", "Model sent for approval."))
    llm.script("comms:quant_associate",
               think("{quant_lead} filed the model. Research needs to hear it from me.", None,
                     ("send_message", {"to": ["er_associate"], "text": f"{t} model is with Stott "
                                       "for approval; hold any figures until it is approved."})),
               think("Passed on.", "Told {er_associate}."))
    llm.script("comms:er_associate",
               think("Quant's model is with Stott. {er_lead} should hold figures.", None,
                     ("send_message", {"to": ["er_lead"], "text": f"{t} model is with Stott for "
                                       "approval; hold figures until it is approved."})),
               think("Passed on.", "Told {er_lead}."))
    llm.script("quant_associate",
               think("Building the workbook from assumptions.yaml, then the formula check.", None,
                     ("build_model", {"ticker": t})),
               think("Built and checked. Now the Monte Carlo and the value drivers.", None,
                     ("run_simulations", {"ticker": t, "runs": 1000})),
               lambda p: _delta_reports(p, t))
    office.assign("er_lead", f"Hand the {t} assumptions to Quant for the model.", title=f"{t} to Quant (demo, real tools)")


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


async def run_demo(office: Office, llm: DemoLLM, pause: float = 6.0, rounds: int | None = None) -> None:
    """Loop the scenes forever (or `rounds` times through), one at a time, with a breather between them."""
    for scene in itertools.cycle(SCENES) if rounds is None else SCENES * rounds:
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
