"""The Captain's tone filter: rewrite a message in a transformational-leadership voice, then
prove nothing important changed.

Two rewriters share one contract, `rewrite(text, recipient) -> str`:
- `LLMRewriter`: Haiku (billed to Juno through the ledger). Used by the real office.
- `RuleRewriter`: deterministic, free. Used by the demo office and whenever the API is off.

`check(original, rewrite)` is plain code, never a model: every ticker, number, date and negation
in the original must survive. The Captain sees any miss before sending, and always chooses
which version goes out; the original is kept in the log either way.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# ---- preservation check --------------------------------------------------------------------
_TICKER = re.compile(r"\$[A-Za-z]{1,5}\b|\b[A-Z][A-Z0-9&.]{1,5}\b")
_NUMBER = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?(?:%|[kKmMbB]\b|bn\b|x\b)?")
_DATE = re.compile(
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    r"|\bmay(?= \d)|\btoday\b|\btomorrow\b|\btonight\b|\bthis week\b|\bnext week\b"
    r"|\beod\b|\bby noon\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|no|never|don't|dont|do not|doesn't|won't|can't|cannot|"
                       r"avoid|stop|without|nothing|none)\b", re.IGNORECASE)
# Upper-case words that are ordinary English, not tickers or terms of art worth guarding.
_COMMON_CAPS = {"I", "A", "OK", "THE", "AND", "OR", "TO", "IS", "IT", "IN", "ON", "OF", "WE",
                "ME", "MY", "BE", "DO", "SO", "AT", "BY", "IF", "UP", "NO", "AM", "PM",
                "ASAP", "FYI", "TBD", "ETA", "PS", "PLS", "THX"}


def _norm_number(tok: str) -> str:
    return tok.replace(",", "").replace("+", "").lower()


@dataclass
class ToneCheck:
    ok: bool
    missing: dict[str, list[str]] = field(default_factory=dict)
    added_numbers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def key_facts(text: str) -> dict[str, list[str]]:
    tickers = sorted({t.lstrip("$").upper() for t in _TICKER.findall(text)
                      if t.lstrip("$").upper() not in _COMMON_CAPS})
    numbers = sorted({_norm_number(n) for n in _NUMBER.findall(text)})
    dates = sorted({d.lower() for d in _DATE.findall(text)})
    return {"tickers": tickers, "numbers": numbers, "dates": dates}


def check(original: str, rewrite: str) -> ToneCheck:
    """Everything that carries an instruction must survive the rewrite."""
    before, after = key_facts(original), key_facts(rewrite)
    after_tickers = {t.upper() for t in re.findall(r"[A-Za-z0-9&]+(?:\.[A-Za-z0-9]+)*", rewrite)}
    missing = {
        "tickers": [t for t in before["tickers"] if t not in after_tickers],
        "numbers": [n for n in before["numbers"] if n not in after["numbers"]],
        "dates": [d for d in before["dates"] if d not in after["dates"]],
    }
    neg_before = len(_NEGATION.findall(original))
    neg_after = len(_NEGATION.findall(rewrite))
    if neg_after < neg_before:
        missing["negations"] = [m.lower() for m in _NEGATION.findall(original)]
    missing = {k: v for k, v in missing.items() if v}
    added = [n for n in after["numbers"] if n not in before["numbers"]]
    return ToneCheck(ok=not missing and not added, missing=missing, added_numbers=added)


# ---- rule-based rewriter (demo, free) --------------------------------------------------------
_SOFTEN = [
    (r"\basap\b", "as soon as you reasonably can"),
    (r"\bimmediately\b", "as a priority"),
    (r"\bright now\b", "as a priority"),
    (r"\bthis is (?:wrong|bad|terrible|awful|garbage|trash)\b", "this isn't where we need it yet"),
    (r"\b(is|are) (?:just |totally |completely )?wrong\b", r"\1 not quite right"),
    (r"\b(?:terrible|awful|garbage|trash|sloppy|lazy|useless|stupid|dumb|pathetic)\b",
     "not up to our standard yet"),
    (r"\bwhy (?:haven't|didn't) you\b", "could you"),
    (r"\bi need you to\b", "could you please"),
    (r"\byou need to\b", "please"),
    (r"\byou must\b", "please make sure to"),
    (r"\bfix (?:this|it)\b", "take another pass at it"),
    (r"\bhurry up\b", "keep the momentum going"),
    (r"!{2,}", "."),
]
_OPENERS_TEAM = [
    "Thanks for the strong work so far.",
    "I appreciate the care you're all putting in.",
    "Really glad to have this team on it.",
    "Great momentum lately.",
]
_OPENERS_ONE = [
    "Thanks for the strong work so far.",
    "I appreciate the care you put into this.",
    "Really glad to have you on this.",
    "Great momentum lately.",
]
_CLOSERS = [
    "This moves us closer to the Fund's mission. Thank you.",
    "I trust your judgment here. Shout if anything blocks you.",
    "Excited to see what you find. Thank you.",
    "Your work here matters. Thanks.",
]


class RuleRewriter:
    engine = "rules"

    async def rewrite(self, text: str, recipient: str) -> str:
        body = text.strip()
        if recipient:   # "Quill, ..." / "Quill ..." at the start: the greeting already says it
            body = re.sub(rf"^(?:hey |hi )?{re.escape(recipient)}\b[,:]?\s*", "", body, flags=re.IGNORECASE)
        for pattern, repl in _SOFTEN:
            body = re.sub(pattern, repl, body, flags=re.IGNORECASE)
        body = re.sub(r"(^|[.?!]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), body)
        if body and body[-1] not in ".?!":
            body += "."
        pick = sum(map(ord, text)) % len(_CLOSERS)   # stable per message
        opener = (_OPENERS_ONE if recipient else _OPENERS_TEAM)[pick]
        greeting = f"Hi {recipient}," if recipient else "Hi team,"
        if not opener.startswith("I "):   # "Hi team, thanks..." but never "i appreciate"
            opener = opener[0].lower() + opener[1:]
        return f"{greeting} {opener}\n\n{body}\n\n{_CLOSERS[pick]}"


# ---- LLM rewriter (real office) -----------------------------------------------------------
TONE_SYSTEM = """You rewrite messages from {captain}, the Captain of an investment fund, to his team of \
AI colleagues, in the voice of a transformational leader: inspiring, appreciative, clear about \
the why, respectful of the team's judgment, and personal to the recipient.

Rules:
- Keep every instruction, request, deadline, ticker, number, name and negation exactly. Never \
drop, soften away or change what is being asked, and never add new asks, facts or numbers.
- Remove harshness, blame and sarcasm; keep urgency where it exists, framed constructively.
- Warm but not gushing: at most one short line of appreciation or purpose.
- Stay close to the original length. Plain text, no emojis, no em dashes.
- Output only the rewritten message."""


class LLMRewriter:
    engine = "llm"

    def __init__(self, office_):
        self.office = office_

    async def rewrite(self, text: str, recipient: str) -> str:
        from HQ.config import model_config
        from HQ.engine.llm import request_params

        office_ = self.office
        office_.ledger.check()
        _, cfg = model_config("associate")
        cfg = {**cfg, "thinking_budget_tokens": 0, "max_tokens": 1024}
        params = request_params(
            cfg, system=TONE_SYSTEM.replace("{captain}", office_.captain_name), tools=[],
            messages=[{"role": "user",
                       "content": f"Recipient: {recipient or 'the whole team'}\n\nMessage:\n{text}"}])
        result = await office_.llm.turn(params=params)
        office_.ledger.record(agent="chief_of_staff", task_id=None, root_id=None,
                              model=params["model"], usage=result.usage,
                              request_id=result.request_id)
        out = "\n".join(b["text"] for b in result.content if b.get("type") == "text").strip()
        return out or text
