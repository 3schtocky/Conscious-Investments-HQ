"""No stub last lines in slide text.

PowerPoint wraps text itself and ignores non-breaking spaces here, so a paragraph can end on a line
with one or two words. We measure the paragraph with the real font, simulate PowerPoint's greedy
wrap, and when the last line would hold fewer than `MIN_LAST` words we put an explicit line break
before the last `MIN_LAST` words, so the last two lines share the load. If the font file is not on
this machine the text is returned untouched. A line break is always safe: it only ever shortens the
line before it.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

MIN_LAST = 3
BREAK = "\v"
_PP = Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts")
_SYS = Path("/System/Library/Fonts/Supplemental")
FILES = {
    ("Calibri", False): [_PP / "Calibri.ttf", Path("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf")],
    ("Calibri", True): [_PP / "Calibrib.ttf", Path("/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf")],
    ("Georgia", False): [_SYS / "Georgia.ttf"],
    ("Georgia", True): [_SYS / "Georgia Bold.ttf"],
}


@lru_cache(maxsize=32)
def _font(name: str, bold: bool, size: int):
    from PIL import ImageFont

    for path in FILES.get((name, bold), []):
        if path.is_file():
            return ImageFont.truetype(str(path), size * 4)   # four times the size for sub-point precision
    return None


def balance(runs: list[tuple[str, bool]], *, font: str, size: float, width_in: float, indent_in: float = 0.0) -> list[tuple[str, bool]]:
    """Runs of (text, bold) for one paragraph, with a BREAK inserted where it keeps the last line to at least MIN_LAST words."""
    fonts = {b: _font(font, b, round(size)) for b in (False, True)}
    if any(f is None for f in fonts.values()) or not runs:
        return runs
    tokens: list[tuple[int, str]] = []          # (run index, word)
    for i, (text, _) in enumerate(runs):
        tokens += [(i, w) for w in text.split(" ") if w]
    if len(tokens) <= MIN_LAST:
        return runs
    limit = (width_in - indent_in) * 72 * 4
    space = fonts[False].getlength(" ")
    lines: list[list[int]] = [[]]
    used = 0.0
    for n, (i, word) in enumerate(tokens):
        w = fonts[runs[i][1]].getlength(word)
        extra = w if not lines[-1] else space + w
        if lines[-1] and used + extra > limit:
            lines.append([n])
            used = w
        else:
            lines[-1].append(n)
            used += extra
    if len(lines) < 2 or len(lines[-1]) >= MIN_LAST:
        return runs
    cut = len(tokens) - MIN_LAST                # the first word of the new last line
    run_i = tokens[cut][0]
    k = sum(1 for i, _ in tokens[:cut] if i == run_i)   # it is this run's k-th word
    result = [list(r) for r in runs]
    text = runs[run_i][0]
    pos = [m.start() for m in re.finditer(r"\S+", text)][k]
    if pos > 0:
        result[run_i][0] = text[:pos].rstrip(" ") + BREAK + text[pos:]
    else:                                       # the break falls between runs: trim the space before it
        result[run_i][0] = BREAK + text
        for r in result[:run_i]:
            r[0] = r[0].rstrip(" ")
    return [(t, b) for t, b in result]
