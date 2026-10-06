"""The investor deck's content contract and its code gate.

A deck is a fixed catalogue of slides (`SLIDES`). Some slides are *data* slides: code fills them
from the approved Quant model and its Model Brief, so their numbers cannot drift. Others are
*narrative* slides: Wren writes a headline, bullets and speaker notes, and every bullet names its
source (a section of the initiating-coverage report, or a part of the Model Brief). Every figure
in the words must exist in the report, the brief or the model, and the usual house rules apply
(approved targets only, no hype, no advice, risks at least as heavy as upside).

`check_copy` is that gate; `hq.deckbuild` renders the .pptx. Nothing here calls a model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from hq import modelbrief, outbox
from hq.engine.runtime import Office
from hq.tools import desk

# id, layout, who fills it, one-line job (Harbor's outline follows this order)
SLIDES: list[tuple[str, str, str, str]] = [
    ("cover", "cover", "data", "Name, rating and base target at a glance."),
    ("thesis", "bullets", "words", "The investment case in four bullets."),
    ("call", "targets", "data", "Rating, price and the bear, base and bull targets."),
    ("business", "bullets", "words", "What the company does and why it matters."),
    ("mispricing", "bullets", "words", "Why we think the market is wrong."),
    ("drivers", "drivers", "data", "The inputs that move the target, in Quant's words."),
    ("forecast", "forecast", "data", "Revenue by case, margins and earnings."),
    ("valuation", "valuation", "data", "What each valuation method says."),
    ("sensitivity", "sensitivity", "data", "How the target moves with the discount rate and exit multiple."),
    ("simulation", "simulation", "data", "The spread of outcomes in 2,000 simulated runs."),
    ("peers", "bullets", "words", "How it compares with its peers."),
    ("catalysts", "bullets", "words", "What could move the stock and when."),
    ("risks", "bullets", "words", "The company-specific risks and their mitigants."),
    ("wrong", "wrong", "data", "What would prove us wrong, in observable terms."),
    ("assumptions", "assumptions", "data", "The model's key assumptions."),
    ("sources", "sources", "data", "Where every number comes from."),
    ("disclosures", "disclosures", "data", "Important disclosures."),
]
IDS = [s[0] for s in SLIDES]
WORDS_SLIDES = [s[0] for s in SLIDES if s[2] == "words"]
UPSIDE_SLIDES = ("thesis", "mispricing", "catalysts")
RISK_SLIDES = ("risks", "wrong")
MAX_HEADLINE_WORDS, MAX_BULLETS, MAX_BULLET_WORDS = 16, 5, 34
MIN_BULLETS = 3

SOURCE = re.compile(r"^(report:(\d\d_[a-z_]+)|brief:(view|drivers|scenarios|sensitivity|breaks)|model:(facts|projections))$")
_YEAR = re.compile(r"\b(?:FY|CY)\s?'?\d{2,4}[EA]?\b|(?<![\w$,.])'\d{2}[EA]?\b|\b[1-4]Q'?\d{2}[EA]?\b|(?<![\w$,.])(?:19|20)\d{2}(?![\d,]|\.\d)")
_FIG = re.compile(r"\(?\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(%|x\b|bn\b|mn\b|tn\b|billion\b|million\b|trillion\b|pts?\b|points?\b)?\)?", re.IGNORECASE)
# The playbook's list of words we do not use (superlatives and urgency), beyond the newsletter gate's own.
_PLAYBOOK_HYPE = re.compile(r"\b(?:massive|explosive|unmissable|game[- ]changer|soaring|skyrocket\w*|can'?t\s+miss|"
                            r"don'?t\s+miss\s+out|now\s+is\s+the\s+time|incredible|spectacular|home\s+run|rocket\w*)\b|!",
                            re.IGNORECASE)
_TAG = re.compile(r"\[(?:S\d+|M|VERIFY[^\]]*)\]")


def coverage_sections(ticker: str) -> dict[str, str]:
    d = desk.coverage_dir(ticker) / "sections"
    return {p.stem: p.read_text(errors="replace") for p in sorted(d.glob("*.md"))} if d.is_dir() else {}


def file_stem(office: Office, ticker: str, kind: str, day: str | None = None) -> str:
    """A deck file's name, built like the report's: CI_<Company-Name>_<TICKER>_<Kind>_<date>. The company part is
    the one on the finished report's own file name, so the two sort side by side."""
    cdir = desk.coverage_dir(ticker)
    found = next((m.group(1) for p in sorted(cdir.glob("CI_*_Initiating-Coverage_*"))
                  if (m := re.fullmatch(rf"CI_(.+)_{re.escape(ticker)}_Initiating-Coverage_.+", p.stem))), None)
    if found is None:
        name = ticker
        mj = cdir / "model.json"
        if mj.is_file():
            name = str(json.loads(mj.read_text()).get("name") or ticker).title()
        found = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    return f"CI_{found}_{ticker}_{kind}_{day or office.ledger.today()}"


def deck_dir(office: Office, ticker: str, day: str | None = None) -> Path:
    return office.outbox_dir / "decks" / ticker / (day or office.ledger.today())


# ---- the number universe ------------------------------------------------------------------------
def figures(text: str) -> list[tuple[float, int, str]]:
    """The figures in prose as (value, decimals shown, unit): things with a $, %, x, scale word or
    decimal point. Years, fiscal-year labels and [S1] tags are not figures; bare integers are not either."""
    text = _TAG.sub(" ", text)
    text = _YEAR.sub(" ", text)
    out = []
    for m in _FIG.finditer(text):
        num, unit = m.group(1), (m.group(2) or "").lower().strip()
        whole = m.group(0)
        if not ("$" in whole or unit or "." in num):
            continue
        decimals = len(num.split(".")[1]) if "." in num else 0
        out.append((float(num.replace(",", "")), decimals, unit))
    return out


def _walk(x, out: list[float]) -> None:
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        out.append(float(x))
    elif isinstance(x, dict):
        for v in x.values():
            _walk(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _walk(v, out)
    elif isinstance(x, str):
        out.extend(v for v, _, _ in figures(x))


def universe(ticker: str, facts: dict) -> list[float]:
    """Every value the words may state: the report's own figures, the brief's facts and the report's model.json."""
    vals: list[float] = []
    for text in coverage_sections(ticker).values():
        vals.extend(v for v, _, _ in figures(text))
    _walk(facts, vals)
    mj = desk.coverage_dir(ticker) / "model.json"
    if mj.is_file():
        _walk(json.loads(mj.read_text()), vals)
    out = set()
    for v in vals:
        for k in (1, 100, 1e-9, 1e-6, 1e-12):   # a fraction shown as a percent, a dollar amount shown in bn, mn or tn
            out.add(abs(v) * k)
    return sorted(out)


def _matches(v: float, decimals: int, unit: str, uni: list[float]) -> bool:
    tol = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(u - v) <= tol for u in uni)   # the universe already holds the bn, mn and tn variants


def untraceable(text: str, uni: list[float]) -> list[str]:
    out = []
    cleaned = _YEAR.sub(" ", _TAG.sub(" ", text))
    for m in _FIG.finditer(cleaned):
        num, unit = m.group(1), (m.group(2) or "").lower().strip()
        if not ("$" in m.group(0) or unit or "." in num):
            continue
        decimals = len(num.split(".")[1]) if "." in num else 0
        if not _matches(float(num.replace(",", "")), decimals, unit, uni):
            out.append(m.group(0).strip(" ()"))
    return out


# ---- the copy ------------------------------------------------------------------------------------
def _p(level: str, code: str, where: str, msg: str) -> dict:
    return {"level": level, "code": code, "where": where, "msg": msg}


def _words(s: str) -> int:
    return len(re.findall(r"\S+", s))


def slide_text(copy: dict, sid: str) -> list[tuple[str, str]]:
    """(where, text) for every piece of prose on a slide."""
    s = (copy.get("slides") or {}).get(sid) or {}
    out = [(f"{sid} headline", str(s.get("headline", "")))]
    for i, b in enumerate(s.get("bullets") or []):
        if isinstance(b, dict):
            out.append((f"{sid} bullet {i + 1}", str(b.get("text", ""))))
            if b.get("detail"):
                out.append((f"{sid} bullet {i + 1} detail", str(b["detail"])))
    for k in ("note", "notes"):
        if s.get(k):
            out.append((f"{sid} {k}", str(s[k])))
    return out


def check_copy(office: Office, ticker: str, copy: dict) -> list[dict]:
    """Every reason this copy may not become a deck (errors) and things worth a second look (warnings)."""
    out: list[dict] = []
    model = office.store.approved_model(ticker)
    if model is None:
        return [_p("error", "no-model", "deck", f"{ticker} has no approved model.")]
    b = modelbrief.load(office, ticker, model["version"])
    if not b or modelbrief.status(office, ticker)["state"] != "ready":
        return [_p("error", "no-brief", "deck", "The approved model has no finished Model Brief.")]
    sections = coverage_sections(ticker)
    if not sections:
        return [_p("error", "no-report", "deck", f"No report sections for {ticker} in its coverage folder.")]
    slides = copy.get("slides")
    if not isinstance(slides, dict):
        return [_p("error", "shape", "deck", "`slides` must be an object keyed by slide id.")]
    for sid in slides:
        if sid not in IDS:
            out.append(_p("error", "unknown-slide", sid, f"No slide called '{sid}'. Slides: {', '.join(IDS)}."))
    for sid, layout, who, _ in SLIDES:
        if sid == "cover":   # the cover carries the subtitle, checked below
            continue
        s = slides.get(sid)
        if not isinstance(s, dict) or not str(s.get("headline", "")).strip():
            out.append(_p("error", "missing", sid, "Needs a headline."))
            continue
        if _words(s["headline"]) > MAX_HEADLINE_WORDS:
            out.append(_p("error", "headline", sid, f"Headline is {_words(s['headline'])} words; keep it to {MAX_HEADLINE_WORDS}."))
        if who == "words":
            bullets = s.get("bullets")
            if not isinstance(bullets, list) or not (MIN_BULLETS <= len(bullets) <= MAX_BULLETS):
                out.append(_p("error", "bullets", sid, f"Needs {MIN_BULLETS} to {MAX_BULLETS} bullets."))
                continue
            for i, bl in enumerate(bullets):
                w = f"{sid} bullet {i + 1}"
                if not isinstance(bl, dict) or not str(bl.get("text", "")).strip():
                    out.append(_p("error", "bullets", w, "Each bullet is {text, source}."))
                    continue
                for field in ("text", "detail"):
                    if str(bl.get(field, "")).rstrip().endswith((".", ";", ":", ",")):
                        out.append(_p("error", "end-punctuation", w, f"The bullet's {field} ends with punctuation. Bullets carry no "
                                                                     "closing period, semicolon, colon or comma."))
                if _words(bl["text"]) > MAX_BULLET_WORDS:
                    out.append(_p("error", "bullet-length", w, f"{_words(bl['text'])} words; keep a bullet to {MAX_BULLET_WORDS}."))
                srcs = bl.get("source")
                srcs = [srcs] if isinstance(srcs, str) else srcs
                if not srcs:
                    out.append(_p("error", "no-source", w, "Name the source: report:<section> or brief:<part>."))
                for src in srcs or []:
                    m = SOURCE.match(str(src))
                    if not m:
                        out.append(_p("error", "bad-source", w, f"'{src}' is not a source. Use report:<section file name>, "
                                                              "brief:view|drivers|scenarios|sensitivity|breaks or model:facts."))
                    elif m.group(2) and m.group(2) not in sections:
                        out.append(_p("error", "bad-source", w, f"'{src}': that report section does not exist. "
                                                              f"Sections: {', '.join(sections)}."))
        elif not s.get("notes"):
            out.append(_p("warning", "notes", sid, "No speaker notes."))
    uni = universe(ticker, b["facts"])
    pub = outbox.publishable(office)
    for sid in slides:
        for where, text in slide_text(copy, sid):
            if not text.strip():
                continue
            out += outbox._check_text(where, text, pub)
            for m in _PLAYBOOK_HYPE.finditer(text):
                out.append(_p("error", "hype", where, f"Not our voice: \"{m.group(0)}\". Measured and institutional: no superlatives, "
                                                      "urgency or exclamation marks."))
            for fig in untraceable(text, uni):
                out.append(_p("error", "untraceable", where, f"The figure {fig} is not in the report, the Model Brief or the "
                                                              "model. Quote only figures from them, exactly."))
    if not str(copy.get("subtitle", "")).strip():
        out.append(_p("error", "missing", "cover", "Needs a `subtitle` for the cover (one line)."))
    else:
        out += outbox._check_text("cover subtitle", str(copy["subtitle"]), pub)
    # Risks at least as prominent as the upside (playbook: risk parity).
    count = lambda ids: sum(_words(t) for sid in ids for _, t in slide_text(copy, sid))
    breaks = sum(_words(str(x)) for x in (b["reading"] or {}).get("breaks", []))
    up, down = count(UPSIDE_SLIDES), count(RISK_SLIDES) + breaks
    if up and down < 0.9 * up:
        out.append(_p("error", "risk-parity", "risks", f"The risks have {down} words against {up} for the upside. Give them at least as much room "
                                                      "as the case for the stock."))
    if model["summary"].get("rating") != "Outperform":
        out.append(_p("error", "rating", "deck", "Decks are made for Outperform names."))
    mj = desk.coverage_dir(ticker) / "model.json"
    if mj.is_file():
        rep = json.loads(mj.read_text())
        mismatch = [k for k in modelbrief.SCENARIOS
                    if abs(rep["scenarios"][k]["price_target"] - b["facts"]["scenarios"][k]["price_target"]) > 0.005]
        if mismatch:
            out.append(_p("error", "report-model", "deck", "The report was written on different targets than the approved model "
                                                            f"({', '.join(mismatch)}). Research must refresh the report first."))
    return out


def errors(problems: list[dict]) -> list[dict]:
    return [p for p in problems if p["level"] == "error"]


# ---- storage -------------------------------------------------------------------------------------
def save_copy(office: Office, ticker: str, copy: dict, *, by: str) -> tuple[Path, list[dict]]:
    problems = check_copy(office, ticker, copy)
    d = deck_dir(office, ticker)
    d.mkdir(parents=True, exist_ok=True)
    model = office.store.approved_model(ticker)
    payload = {"ticker": ticker, "date": office.ledger.today(), "model_version": model["version"] if model else None,
               "by": by, "copy": copy, "problems": problems, "clean": not errors(problems)}
    (d / "copy.json").write_text(json.dumps(payload, indent=1))
    return d / "copy.json", problems


def load_copy(office: Office, ticker: str, day: str | None = None) -> dict | None:
    p = deck_dir(office, ticker, day) / "copy.json"
    return json.loads(p.read_text()) if p.is_file() else None
