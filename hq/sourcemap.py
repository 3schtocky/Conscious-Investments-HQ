"""Source map: tie every figure in a deliverable to a source, by code, for free.

`extract_figures` pulls the figures a reader would take as fact out of a text (dollar amounts,
percentages, multiples, scaled numbers like "1.2 billion"), skipping years, dates, quarter labels,
footnote markers and bare small counts. `SourcePool` holds the numbers found in a ticker's real
sources (the approved model, Quant's brief, the facts folder and filings). `tie_out` matches the
two with rounding-aware comparison: a figure written with k decimals matches a source value that
rounds to it. What matches is cleared with no model call; what does not is an exception that
Tally traces and Vera rules on (`hq/finalaudit.py`).
"""

from __future__ import annotations

import ast
import itertools
import json
import math
import operator
import re
from dataclasses import dataclass, field

_SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "mil": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9,
           "billion": 1e9, "t": 1e12, "trillion": 1e12}
_NUM = re.compile(
    r"(?P<cur>[$€£]\s?)?(?P<neg>[-−(])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<unit>%|x\b|bps\b|basis points\b|(?:thousand|million|billion|trillion|mil|mm|mn|bn|[kmbt])\b))?",
    re.IGNORECASE)
_SKIP_BEFORE = re.compile(r"(?:\b(?:Q|FY|H|CY|v|No\.?|#|Fig\.?|Figure|Table|Note|Section|§)\s?|\[|\w)$",
                          re.IGNORECASE)
_DATE_AFTER = re.compile(r"^\s?(?:/\d|-\d{1,2}-\d|(?:st|nd|rd|th)\b)")
_MONTH_BEFORE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s$", re.IGNORECASE)


@dataclass(frozen=True)
class Figure:
    text: str          # as written, e.g. "$1.2 billion"
    value: float       # in the unit a reader sees: dollars, or percent points, or a multiple
    decimals: int      # decimals as written (rounding precision)
    kind: str          # money | percent | multiple | number | bps
    scale: float       # the scale word applied (1 when none): decimals refer to scaled units
    where: str         # section heading
    sentence: str


@dataclass
class Tie:
    figure: Figure
    source: str | None = None   # where it matched; None = exception


@dataclass
class SourcePool:
    values: list[tuple[float, str]] = field(default_factory=list)   # (value, source label)

    def add_number(self, v: float, label: str) -> None:
        if math.isfinite(v):
            self.values.append((float(v), label))

    def add_json(self, obj, label: str) -> None:
        stack = [obj]
        while stack:
            o = stack.pop()
            if isinstance(o, bool):
                continue
            if isinstance(o, (int, float)):
                self.add_number(o, label)
            elif isinstance(o, dict):
                stack.extend(o.values())
            elif isinstance(o, (list, tuple)):
                stack.extend(o)
            elif isinstance(o, str):
                self.add_text(o, label)

    def add_text(self, text: str, label: str) -> None:
        for f in extract_figures(text, keep_all=True):
            self.add_number(f.value, label)

    def add_csv(self, text: str, label: str) -> None:
        for tok in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text):
            try:
                self.add_number(float(tok), label)
            except ValueError:
                pass

    def add_source(self, name: str, text: str) -> None:
        label = name
        if name.endswith(".json"):
            try:
                self.add_json(json.loads(text), label)
                return
            except ValueError:
                pass
        if name.endswith(".csv"):
            self.add_csv(text, label)
        else:
            self.add_text(text, label)


def _decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0


def _flush(prose: list[str], units: list[str]) -> None:
    if prose:
        units.append(" ".join(prose))
        prose.clear()


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _sections(text: str):
    """Yield (heading, sentence) for the prose of a markdown text. Each table row and list item
    stands alone, so a figure is judged against the row or bullet it sits in."""
    heading = ""
    for block in re.split(r"\n\s*\n", text):
        prose: list[str] = []
        units: list[str] = []

        for ln in block.strip().splitlines():
            if ln.lstrip().startswith("#"):
                _flush(prose, units)
                heading = ln.lstrip("# ").strip()
            elif ln.lstrip().startswith("|"):
                _flush(prose, units)
                if not re.fullmatch(r"[\s|:\-]+", ln):          # skip the |---|---| rule
                    units.append(" | ".join(c.strip() for c in ln.strip().strip("|").split("|")) + ".")
            elif _LIST_ITEM.match(ln):
                _flush(prose, units)
                prose.append(_LIST_ITEM.sub("", ln).strip())
            elif ln.strip():
                prose.append(ln.strip())
        _flush(prose, units)
        for unit in units:
            unit = re.sub(r"https?://\S+", " ", unit)
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\[(\"'*$])", unit):
                if s.strip():
                    yield heading, s.strip()


def extract_figures(text: str, *, keep_all: bool = False) -> list[Figure]:
    """Figures in `text`. With `keep_all` (used on sources) nothing is skipped as noise."""
    out: list[Figure] = []
    for heading, sent in _sections(text):
        for m in _NUM.finditer(sent):
            num, unit, cur = m.group("num"), (m.group("unit") or "").lower(), m.group("cur")
            before = sent[:m.start()]
            if not keep_all:
                if not cur and _SKIP_BEFORE.search(before):
                    continue
                if _DATE_AFTER.match(sent[m.end():]) or _MONTH_BEFORE.search(before):
                    continue
            raw = float(num.replace(",", ""))
            dec = _decimals(num)
            plain = not cur and not unit and "," not in num
            if not keep_all and plain and dec == 0 and (raw <= 31 or 1900 <= raw <= 2100):
                continue   # a count, a day or a year, not a figure
            if unit == "%":
                kind, value = "percent", raw
            elif unit == "x":
                kind, value = "multiple", raw
            elif unit in ("bps", "basis points"):
                kind, value = "bps", raw
            elif unit:
                kind, value = "money" if cur else "number", raw * _SCALES[unit]
            else:
                kind, value = ("money" if cur else "number"), raw
            if m.group("neg") in ("-", "−") and not before.endswith("-"):
                value = -value
            out.append(Figure(m.group(0).strip(), value, dec, kind, _SCALES.get(unit, 1.0), heading, sent))
    return out


def _match(f: Figure, v: float) -> bool:
    shown, v = abs(f.value), abs(v)
    tol = 0.5 * 10 ** -f.decimals * f.scale + 1e-9
    if f.kind == "percent":   # a source may hold 12.3 (points) or 0.123 (a fraction)
        return abs(shown - v) <= tol or abs(shown - v * 100) <= tol
    if f.kind == "bps":
        return abs(shown - v) <= tol or abs(shown - v * 1e4) <= tol
    if f.kind == "multiple":
        return abs(shown - v) <= tol
    # money and counts: a filing may hold the value in units, thousands, millions or billions
    return any(abs(shown - v * m) <= tol for m in (1.0, 1e3, 1e6, 1e9))


_SCALES_DOWN = (1.0, 1e-3, 1e-6, 1e-9, 1e-12, 1e3)
_AST_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _AST_OPS:
        return _AST_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    raise ValueError("not plain arithmetic")


def formula_value(sentence: str) -> tuple[float, str] | None:
    """The value of an arithmetic expression the text shows ("our arithmetic: $807 mn / 3.4 GWh"):
    the text after the marker up to the parenthesis that closes it, reduced to numbers and + - * /."""
    m = re.search(r"arithmetic\s*:?\s*", sentence, re.IGNORECASE)
    if not m:
        return None
    depth, end = 0, len(sentence)
    for i in range(m.end(), len(sentence)):
        c = sentence[i]
        depth += (c == "(") - (c == ")")
        if depth < 0:
            end = i
            break
    raw = sentence[m.end():end].strip()
    expr = re.sub(r"(?<=\d),(?=\d{3})", "", raw.replace("−", "-").replace("×", "*").replace("÷", "/"))
    scaled = re.sub(r"(\d(?:\.\d+)?)\s?(?:mn|mm|million)\b", r"(\1*1000000)", expr, flags=re.IGNORECASE)
    scaled = re.sub(r"(\d(?:\.\d+)?)\s?(?:bn|billion)\b", r"(\1*1000000000)", scaled, flags=re.IGNORECASE)
    scaled = re.sub(r"[A-Za-z%$]+", "", scaled)
    scaled = re.sub(r"\s+", " ", scaled).strip().rstrip(".")
    if not re.search(r"\d\s*[-+*/]\s*\(?-?\d|\)\s*[-+*/]", scaled):
        return None
    try:
        return _eval(ast.parse(scaled, mode="eval")), raw
    except (ValueError, SyntaxError, ZeroDivisionError, RecursionError):
        return None


def _results(a: float, b: float) -> list[tuple[float, str]]:
    out = [(a + b, f"{a:g} + {b:g}"), (a - b, f"{a:g} - {b:g}"), (a * b, f"{a:g} x {b:g}")]
    if b:
        out += [(a / b, f"{a:g} / {b:g}"), (a / b - 1, f"{a:g} / {b:g} - 1")]
    return out


def derived(f: Figure, siblings: list[Figure], sentence: str) -> str | None:
    """Is this figure plain arithmetic on figures in its own sentence, or on the formula the
    sentence shows? Returns how, or None. Operands are not trusted here: an operand with no source
    is its own exception, so a derived pass only clears the derived figure."""
    candidates: list[tuple[float, str]] = []
    fv = formula_value(sentence)
    if fv:
        candidates.append((fv[0], fv[1]))
    others = [o for o in siblings if o is not f]
    for a, b in itertools.permutations(others[:10], 2):
        candidates += [(r, f"{a.text} and {b.text}: {how}") for r, how in _results(a.value, b.value)]
    for r, how in candidates:
        if math.isfinite(r) and any(_match(f, r * k) for k in _SCALES_DOWN):
            return how
    return None


def tie_out(figures: list[Figure], pool: SourcePool) -> list[Tie]:
    by_sentence: dict[str, list[Figure]] = {}
    for f in figures:
        by_sentence.setdefault(f.sentence, []).append(f)
    ties = []
    for f in figures:
        hit = next((label for v, label in pool.values if _match(f, v)), None)
        if hit is None and (how := derived(f, by_sentence[f.sentence], f.sentence)):
            hit = f"derived ({how})"
            pool.add_number(f.value, hit)   # the same figure repeated elsewhere is the same arithmetic
        ties.append(Tie(f, hit))
    return ties


def exceptions(ties: list[Tie]) -> list[Figure]:
    return [t.figure for t in ties if t.source is None]
