"""The delegates' comms tools, and the routing rules for who may message whom.

Leads build; their delegates (the wing's associate) speak for the wing. A lead's `send_message`
reaches only its own delegate (and Juno). Delegates talk to each other, ask each other for
status, and hand real work to another wing's lead as a structured request.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from HQ.digest import wing_digest
from HQ.engine.guards import GuardBlock
from HQ.tools.office import POST_TO_GROUP, SEND_MESSAGE, Tool, ToolContext, _str

if TYPE_CHECKING:
    from HQ.engine.runtime import Office

JUNO = "chief_of_staff"


# routing ----------------------------------------------------------------------------------
def check_route(office: Office, sender: str, recipients: list[str]) -> None:
    """Refuse a message that skips the wing's delegate. Audit and Juno are exempt both ways:
    Vera keeps her direct line to anyone, and anyone may reach Juno or Audit."""
    s = office.agents[sender]
    if sender == JUNO or s.wing == "audit":
        return
    for r in recipients:
        t = office.agents[r]
        if r == JUNO or t.wing == "audit":
            continue
        if s.tier == "lead":
            mine = office.delegate_of(s.wing)
            if mine is None or r != mine.id:
                name = mine.nickname if mine else "your delegate"
                raise GuardBlock(
                    f"Leads talk to their own delegate. Tell {name} what {t.nickname}'s wing needs "
                    f"to know: {name} speaks for the wing and already sees what you are doing. "
                    "Don't spend your turns explaining.")
        elif t.tier == "lead" and t.wing != s.wing:
            raise GuardBlock(
                f"Don't message {t.nickname} directly. Ask their delegate with `ask_delegate`, "
                "or hand over real work with `relay_request`.")


# tools ------------------------------------------------------------------------------------
async def _wing_status(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    who = inp.get("wing")
    if who:
        key = str(who).strip().lower()
        wing = next((w for w in office._wings
                     if key in (w.lower(), office.wing_name(w).lower())), None)
        if wing is None:
            try:
                wing = office.agents[office.resolve(key)].wing
            except KeyError:
                raise GuardBlock(f"No wing {who!r}. Wings: "
                                 + ", ".join(office.wing_name(w) for w in office._wings)) from None
    else:
        wing = ctx.agent.wing
    d = wing_digest(office, wing)
    return json.dumps({k: d[k] for k in ("name", "state", "headline", "blockers",
                                         "waiting_on_captain", "people")}, indent=1)


WING_STATUS = Tool(
    name="wing_status",
    description=("Where a wing stands right now, read from the live activity log: who is on what, "
                 "the last steps taken, anything blocked or waiting on {captain}. Free. Leave "
                 "`wing` empty for your own wing."),
    input_schema={"type": "object",
                  "properties": {"wing": {"type": "string",
                                          "description": "A wing name or a colleague in it."}}},
    handler=_wing_status,
)


async def _ask_delegate(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    to = office.resolve(_str(inp, "to", max_len=100))
    target = office.agents[to]
    if to == ctx.agent.id:
        raise GuardBlock("That is you.")
    if target.tier != "associate" or target.wing == "executive":
        theirs = office.delegate_of(target.wing)
        raise GuardBlock(f"{target.nickname} is not a wing delegate. Ask the delegate of their "
                         f"wing ({theirs.nickname if theirs else 'none'}).")
    question = _str(inp, "question", max_len=1500)
    return await office.comms.ask(to, ctx.agent.id, question, depth=ctx.depth)


ASK_DELEGATE = Tool(
    name="ask_delegate",
    description=("Ask another wing's delegate a question (status, whether a file or model is "
                 "ready, who owns something) and get the answer back now. The delegate answers from "
                 "its lead's live activity, so the lead is never interrupted. For real work, use "
                 "`relay_request` instead."),
    input_schema={"type": "object",
                  "properties": {"to": {"type": "string", "description": "A delegate id or nickname."},
                                 "question": {"type": "string"}},
                  "required": ["to", "question"]},
    handler=_ask_delegate,
)


async def _relay_request(ctx: ToolContext, inp: dict) -> str:
    office = ctx.office
    key = _str(inp, "to", max_len=100)
    try:
        who = office.resolve(key)
        wing = office.agents[who].wing
    except KeyError:
        wing = next((w for w in office._wings if key.lower() in (w.lower(),
                                                                 office.wing_name(w).lower())), "")
    lead = office.lead_of(wing)
    if lead is None:
        raise GuardBlock(f"No wing lead found for {key!r}.")
    if wing == ctx.agent.wing:
        raise GuardBlock("That is your own wing: tell your lead with `send_message`.")
    need = _str(inp, "need", max_len=3000)
    why = _str(inp, "why", required=False, max_len=1500)
    deliverable = _str(inp, "deliverable", max_len=1500)
    mine = office.lead_of(ctx.agent.wing)
    title = need.splitlines()[0][:100]
    if any(t["assignee"] == lead.id and t["assigned_by"] == ctx.agent.id and t["title"] == title
           for t in office.store.open_tasks()):
        raise GuardBlock(f"You already asked {lead.nickname} for this and it is still open.")
    body = (f"Request from {ctx.agent.nickname}, delegate for {mine.nickname if mine else 'a wing'} "
            f"({office.wing_name(ctx.agent.wing)}):\n\nNeed: {need}\n"
            + (f"Why: {why}\n" if why else "") + f"Deliverable: {deliverable}\n\n"
            f"When it is done, tell your delegate {office.delegate_of(wing).nickname} in one line "
            f"where the deliverable is; they pass it on. Don't write {ctx.agent.nickname} a report.")
    task_id = office.assign(lead.id, body, title=title, by=ctx.agent.id)
    return f"Request sent to {lead.nickname} (task #{task_id})."


RELAY_REQUEST = Tool(
    name="relay_request",
    description=("Hand a piece of real work to another wing's lead, as a structured request: what "
                 "is needed, why, and exactly what should come back. Use it when a colleague in your "
                 "wing needs something another wing must build."),
    input_schema={"type": "object",
                  "properties": {"to": {"type": "string", "description": "A wing, or someone in it."},
                                 "need": {"type": "string"}, "why": {"type": "string"},
                                 "deliverable": {"type": "string",
                                                 "description": "What should come back, and where."}},
                  "required": ["to", "need", "deliverable"]},
    handler=_relay_request,
)


async def _walk_the_floor(ctx: ToolContext, inp: dict) -> str:
    result = await ctx.office.rounds.walk(reason="asked")
    return result["summary"]


WALK_THE_FLOOR = Tool(
    name="walk_the_floor",
    description=("Do your rounds now: walk to each busy wing's delegate, ask where the wing stands, "
                 "and get the roll-up back. Use it when {captain} asks where things are. It works "
                 "while the office is paused too (then it reads the live activity log, with no "
                 "model turns). Follow it with `report_to_captain`."),
    input_schema={"type": "object", "properties": {}},
    handler=_walk_the_floor,
)


def comms_tools(depth: int = 0) -> list[Tool]:
    """A delegate's comms tools. A delegate asked by another delegate (depth 1) cannot ask a
    third, so questions never chain across the office."""
    tools = [WING_STATUS, SEND_MESSAGE, RELAY_REQUEST, POST_TO_GROUP]
    return tools if depth >= 1 else [WING_STATUS, ASK_DELEGATE, *tools[1:]]
