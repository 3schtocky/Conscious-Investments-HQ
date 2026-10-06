"""Build the Micron demo that visitors play: `uv run hq export-demo-micron`.

Records the scripted run through the real engine (`HQ.demo_micron`), re-times it onto five minutes
(`HQ.demo_script`), and writes everything the browser needs to `web/public/demo/micron/`:

    tour.json        the timed script
    report.json      the 11 report sections as safe HTML (charts and tables included)
    model.json       the three cases, the projections, the simulation range and the value drivers
    note.json        the client note (article, X and LinkedIn posts)
    files/           the report PDF, the model workbook, the note's header image

Every page carries the DEMO label and the disclaimer. The text of each deliverable is the real file
the agents worked on: the report is the erb initiation, the model is the formula workbook the Quant
tools built and checked, the note is the issue that passed the publishing gate.
"""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

from HQ import demo_micron, demo_script
from HQ.config import ERB_DIR, ROOT

OUT = ROOT / "web" / "public" / "demo" / "micron"
COVERAGE = ERB_DIR / "coverage" / "MU"
BANNER = ("Demo: Micron (MU) illustrative research, not investment advice. Real SEC data and a real "
          "model engine; the agents are scripted and no AI model was called.")
CHARTS = {"price_targets": "price_targets.png", "football_field": "football_field.png",
          "band_pe": "band_ltm_pe.png", "band_ev_ebitda": "band_ltm_ev_ebitda.png",
          "product_mix": "mix_product.png", "geography_mix": "mix_geography.png",
          "financial_grid": "financial_grid.png"}
CHART_TITLE = {"price_targets": "MU price target scenarios", "football_field": "MU valuation football field",
               "band_pe": "MU LTM P/E against its medians", "band_ev_ebitda": "MU LTM EV/EBITDA against its medians",
               "product_mix": "MU FY'25 revenue by product", "geography_mix": "MU FY'25 revenue by geography",
               "financial_grid": "MU financial summary charts"}
TAG = re.compile(r"\s?\[(?:S\d+(?:\s*,\s*S\d+)*|M)\]")


# ---- a small Markdown subset (headings, paragraphs, bullets, bold, italics, tables) --------------
def _inline(text: str) -> str:
    text = html.escape(TAG.sub("", text), quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", text)


def _table(rows: list[list[str]], *, head: bool = True) -> str:
    out = ["<table>"]
    for i, row in enumerate(rows):
        tag = "th" if head and i == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{html.escape(str(c))}</{tag}>" for c in row) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _usd(x: float, *, unit: str = "bn") -> str:
    return f"${x / 1e9:,.1f} bn" if unit == "bn" else f"${x:,.2f}"


def _pct(x: float) -> str:
    return f"({abs(x) * 100:.1f}%)" if x < 0 else f"{x * 100:.1f}%"


def _exhibit(token: str, model: dict) -> str:
    """An exhibit token from the erb scaffold, as a chart image or a table built from model.json."""
    if token in CHARTS:
        return (f'<figure><img src="img/{CHARTS[token]}" alt="{CHART_TITLE[token]}" loading="lazy">'
                f"<figcaption>{CHART_TITLE[token]}. Source: SEC filings, Yahoo Finance, Conscious Investments model."
                "</figcaption></figure>")
    sc = model["scenarios"]
    if token == "company_snapshot":
        c = model["cover"]
        rows = [["Price", f"${c['price']:,.2f}"], ["52-week range", f"${c['week52_low']:,.2f} to ${c['week52_high']:,.2f}"],
                ["Market cap", _usd(c["market_cap"])], ["YTD return", _pct(c["ytd_return"])],
                ["NTM P/E", f"{c['ntm_pe']:.1f}x"], ["NTM EV/EBITDA", f"{c['ntm_ev_ebitda']:.1f}x"],
                ["ROIC", _pct(c["roic"])]]
        return f"<h4>MU company snapshot</h4>{_table(rows, head=False)}"
    if token in ("dcf", "assumptions"):
        head = ["", "Bear", "Base", "Bull"]
        rows = [head,
                ["WACC", *[_pct(sc[k]["wacc"]["wacc"]) for k in ("bear", "base", "bull")]],
                ["DCF value per share", *[f"${sc[k]['methods']['dcf']:,.2f}" for k in ("bear", "base", "bull")]],
                ["Forward P/E value", *[f"${sc[k]['methods']['pe']:,.2f}" for k in ("bear", "base", "bull")]],
                ["EV/EBITDA value", *[f"${sc[k]['methods']['ev_ebitda']:,.2f}" for k in ("bear", "base", "bull")]],
                ["Blended price target (50/25/25)", *[f"${sc[k]['price_target']:,.2f}" for k in ("bear", "base", "bull")]],
                ["Implied return", *[_pct(sc[k]["total_return"]) for k in ("bear", "base", "bull")]]]
        return f"<h4>MU valuation summary</h4>{_table(rows)}"
    if token == "sensitivity":
        s = model["sensitivity"]
        rows = [["WACC \\ exit EV/EBITDA", *[f"{c:.1f}x" for c in s["cols"]]]]
        for w, vals in zip(s["rows_wacc"], s["values"], strict=True):
            rows.append([_pct(w), *[f"${v:,.2f}" for v in vals]])
        return f"<h4>MU DCF price target sensitivity</h4>{_table(rows)}"
    if token == "financial_summary":
        b = model["projections"]["base"]
        rows = [["", *[f"FY'{str(r['fy'])[2:]}E" for r in b]],
                ["Revenue", *[_usd(r["revenue"]) for r in b]],
                ["  YoY", *[_pct(r["revenue_growth"]) for r in b]],
                ["EBITDA margin", *[_pct(r["ebitda_margin"]) for r in b]],
                ["Diluted EPS", *[f"${r['eps']:,.2f}" for r in b]],
                ["Free cash flow", *[_usd(r["ufcf"]) for r in b]]]
        return f"<h4>MU base-case financial summary</h4>{_table(rows)}"
    return ""   # comps and any other table the demo run does not use


def markdown(text: str, model: dict) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)   # scaffold guidance, including multi-line comments
    out, para, rows, bullets = [], [], [], []

    def flush() -> None:
        nonlocal para, rows, bullets
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
        if bullets:
            out.append("<ul>" + "".join(f"<li>{_inline(b)}</li>" for b in bullets) + "</ul>")
        if rows:
            body = [r for r in rows if not re.fullmatch(r"[\s|:\-]+", r)]
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in body]
            out.append(_table(cells))
        para, rows, bullets = [], [], []

    for line in text.splitlines():
        s = line.strip()
        if re.fullmatch(r"\{\{(\w+)\}\}", s):
            flush()
            out.append(_exhibit(s[2:-2], model))
        elif s.startswith("# "):
            flush()
            out.append(f"<h2>{_inline(s[2:])}</h2>")
        elif s.startswith("## "):
            flush()
            out.append(f"<h3>{_inline(s[3:])}</h3>")
        elif s.startswith("- "):
            if para or rows:
                flush()
            bullets.append(s[2:])
        elif s.startswith("|"):
            if para or bullets:
                flush()
            rows.append(s)
        elif not s:
            flush()
        else:
            if bullets or rows:
                flush()
            para.append(s)
    flush()
    return "\n".join(o for o in out if o)


def _front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.split("#")[0].strip().strip('"')
    return meta, body.lstrip("\n")


def report(model: dict) -> dict:
    sections = []
    for path in sorted((COVERAGE / "sections").glob("*.md")):
        text = path.read_text()
        meta, body = _front_matter(text)
        if path.stem.startswith("00"):   # the cover: tagline, then the thesis bullets and the overview
            body = f"<p class=\"tagline\">{html.escape(meta.get('tagline', ''))}</p>\n" + markdown(body, model)
            title = "Cover"
            sections.append({"id": path.stem, "title": title, "html": body})
            continue
        title = re.match(r"#\s+(.+)", body).group(1) if re.match(r"#\s+(.+)", body) else path.stem
        sections.append({"id": path.stem, "title": title.title() if title.isupper() else title,
                         "html": markdown(body, model)})
    holdings = _front_matter((COVERAGE / "sections" / "00_cover.md").read_text())[0].get("holdings_disclosure", "")
    disclaimer = (ERB_DIR / "guides" / "disclaimer.md").read_text().split("-->", 1)[-1]
    disclaimer = disclaimer.replace("{{HOLDINGS_DISCLOSURE}}", holdings)
    sections.append({"id": "disclaimer", "title": "Disclaimer", "html": markdown(disclaimer, model)})
    return {"title": f"{model['name']} (MU): Initiating Coverage", "date": model["cover"]["as_of"],
            "rating": model["rating"]["rating"], "banner": BANNER, "sections": sections,
            "pdf": "files/MU_Initiating-Coverage_2026-10-05.pdf"}


# ---- the model ---------------------------------------------------------------------------------
def model_view(model: dict, events: list[dict]) -> dict:
    sims = {}
    for ev in events:
        if ev["type"] == "tool_result" and ev.get("tool") == "run_simulations":
            sims = json.loads(ev["text"])
    cases = {}
    for k in ("bear", "base", "bull"):
        s = model["scenarios"][k]
        cases[k] = {"price_target": round(s["price_target"], 2), "total_return": round(s["total_return"], 4),
                    "wacc": round(s["wacc"]["wacc"], 4),
                    "methods": {m: round(v, 2) for m, v in s["methods"].items()},
                    "projections": [{"fy": r["fy"], "revenue": round(r["revenue"] / 1e9, 1),
                                    "growth": round(r["revenue_growth"], 4),
                                    "ebitda_margin": round(r["ebitda_margin"], 4), "eps": round(r["eps"], 2),
                                    "capex": round(r["capex"] / 1e9, 1), "fcf": round(r["ufcf"] / 1e9, 1)}
                                   for r in model["projections"][k]]}
    return {"banner": BANNER, "ticker": "MU", "price": model["price"], "as_of": model["cover"]["as_of"],
            "rating": model["rating"]["rating"], "benchmark_return": round(model["rating"]["benchmark_return"], 4),
            "excess_return": round(model["rating"]["excess_return"], 4), "horizon_months": model["horizon_months"],
            "target_date": model["scenarios"]["base"]["target_date"], "cases": cases,
            "football_field": model["football_field"], "sensitivity": model["sensitivity"],
            "simulation": {"runs": sims.get("runs"), "p10_p50_p90": sims.get("price_target_p10_p50_p90"),
                           "chance_above_price": sims.get("chance_above_price"),
                           "chance_beats_sp500": sims.get("chance_beats_sp500_benchmark"),
                           "drivers": sims.get("top_value_drivers", [])},
            "workbook": "files/MU_model_v1.xlsx"}


def position(office, model: dict) -> dict:
    """The open paper position, as the Portfolio tab shows it in the demo."""
    from HQ import portfolio

    p = office.store.positions("open")[0]
    board = portfolio.scoreboard(office, {"MU": p["entry_price"], portfolio.benchmark(): p["spy_at_entry"]})
    row = board["open"][0]
    base = model["scenarios"]["base"]
    return {"banner": BANNER, "ticker": p["ticker"], "name": "Micron Technology, Inc.", "size_pct": p["size_pct"],
            "entry_price": round(p["entry_price"], 2), "shares": round(p["shares"], 3),
            "value": round(p["entry_value"], 2), "portfolio_value": round(board["value"], 2),
            "entered": row["entry_day"], "rating": model["rating"]["rating"],
            "base_target": round(base["price_target"], 2), "to_target": round(base["price_return"], 4),
            "bear_target": round(model["scenarios"]["bear"]["price_target"], 2),
            "bull_target": round(model["scenarios"]["bull"]["price_target"], 2),
            "model_version": p["model_version"], "thesis": p["thesis"], "proposed_by": p["proposed_by"],
            "return": 0.0, "vs_sp500": 0.0,
            "downloads": [{"label": "Download the report (.docx)", "href": "files/MU_Initiating-Coverage_2026-10-05.docx"},
                          {"label": "Download the model (.xlsx)", "href": "files/MU_model_v1.xlsx"},
                          {"label": "Report as a PDF", "href": "files/MU_Initiating-Coverage_2026-10-05.pdf"}]}


def export(out: Path = OUT) -> dict:
    model = json.loads((COVERAGE / "model.json").read_text())
    events, office = demo_micron.record()
    names = {a.id: a.nickname for a in office.agents.values()}
    tour = demo_script.build(events, names)
    issue_dir = next((demo_micron.RECORD_DIR / "outbox" / "newsletters").iterdir())
    shutil.rmtree(out, ignore_errors=True)
    (out / "files").mkdir(parents=True)
    (out / "img").mkdir()
    for f in CHARTS.values():
        shutil.copyfile(COVERAGE / "charts" / f, out / "img" / f)
    pdf = next(COVERAGE.glob("CI_*Initiating-Coverage*.pdf"))
    shutil.copyfile(pdf, out / "files" / "MU_Initiating-Coverage_2026-10-05.pdf")
    shutil.copyfile(next(COVERAGE.glob("CI_*Initiating-Coverage*.docx")), out / "files" / "MU_Initiating-Coverage_2026-10-05.docx")
    shutil.copyfile(demo_micron.RECORD_DIR / "quant" / "MU" / "MU_model_v1.xlsx", out / "files" / "MU_model_v1.xlsx")
    shutil.copyfile(issue_dir / "header.png", out / "files" / "note_header.png")
    meta = json.loads((issue_dir / "meta.json").read_text())
    social = (issue_dir / "social.md").read_text()
    note = {"banner": BANNER, "title": meta["title"], "date": meta.get("date", ""),
            "words": meta["words"], "html": (issue_dir / "body.html").read_text(),
            "social": social, "header": "files/note_header.png"}
    model["name"] = "Micron Technology, Inc."
    for name, body in {"tour": tour, "report": report(model), "model": model_view(model, events),
                       "note": note, "position": position(office, model)}.items():
        (out / f"{name}.json").write_text(json.dumps(body, separators=(",", ":")))
    summary = {"beats": len(tour["beats"]), "duration": tour["duration"], "unlocks": tour["unlocks"], "out": str(out)}
    print(f"Micron demo -> {out}: {summary['beats']} beats, {summary['duration']:.0f}s")
    return summary
