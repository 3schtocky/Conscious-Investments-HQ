"""Quant's Excel model: a live-formula workbook that re-derives erb's valuation.

Layout (the Quant charter's standard):
- **Inputs**: every assumption, in blue, with the reason from assumptions.yaml beside it.
- **Calc Base / Calc Bull / Calc Bear**: projections, WACC, DCF, multiples and the price
  target, all Excel formulas that reference Inputs. No numbers are typed into calculations
  (only the structural 0, 1 and 2).
- **Outputs**: bull/base/bear price targets and returns, the method blend, the rating, and a
  WACC x terminal sensitivity grid, all formulas.

`build()` writes the workbook; `check()` recomputes every formula with our own evaluator and
compares the results with `erb.model.run()` on the same assumptions. A model only goes to the
Captain if the check passes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml
from erb import model as erb_model
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from hq.quant.formula import WorkbookEvaluator, hardcoded_numbers

SCENARIOS = ("base", "bull", "bear")
SHEET = {"base": "Calc Base", "bull": "Calc Bull", "bear": "Calc Bear"}

BLUE = Font(name="Calibri", color="0000FF")          # inputs
BLACK = Font(name="Calibri", color="000000")         # formulas
GREEN = Font(name="Calibri", color="007A33")         # links to another sheet
BOLD = Font(name="Calibri", bold=True)
TITLE = Font(name="Calibri", bold=True, size=14)
HEAD_FILL = PatternFill("solid", fgColor="2E2C29")
HEAD_FONT = Font(name="Calibri", bold=True, color="FFFFFF")
INPUT_FILL = PatternFill("solid", fgColor="FFF8E1")
SECTION_FILL = PatternFill("solid", fgColor="ECEAE1")
THIN = Border(bottom=Side(style="thin", color="B9B5AD"))

FMT = {"usd": '#,##0;(#,##0)', "usd_m": '#,##0,,"m";(#,##0,,"m")', "px": '$#,##0.00',
       "pct": '0.0%', "x": '0.0"x"', "num": '#,##0.00', "date": 'yyyy-mm-dd', "int": '0',
       "shares": '#,##0,,"m"', "factor": '0.0000'}


# ---- assumptions ----------------------------------------------------------------------------
def load_assumptions(path: Path) -> tuple[dict, dict]:
    """The assumptions dict (as erb reads it) and the `why:` notes per key, from the comments."""
    text = path.read_text()
    a = yaml.safe_load(text)
    notes = {}
    for line in text.splitlines():
        stripped = line.strip()
        if ":" in stripped and "#" in stripped and not stripped.startswith("#"):
            key = stripped.split(":", 1)[0].strip().strip('"')
            notes.setdefault(key, stripped.split("#", 1)[1].strip())
    return a, notes


def _vec(x, n):
    return [float(v) for v in erb_model._vec(x, n)]


# ---- builder ------------------------------------------------------------------------------
@dataclass
class Sheet:
    ws: object
    row: int = 1
    names: dict = field(default_factory=dict)


class ModelWorkbook:
    def __init__(self, a: dict, notes: dict | None = None, *, version: int = 1,
                 prepared_by: str = "Quant Department"):
        self.a = copy.deepcopy(a)
        self.notes = notes or {}
        self.n = int(self.a["projection_years"])
        self.version = version
        self.prepared_by = prepared_by
        self.results = erb_model.run(self.a)          # the engine's answer, for the check
        self.wb = Workbook()
        self.cols = [get_column_letter(3 + i) for i in range(self.n)]   # C.. for years
        self.note_col = get_column_letter(3 + self.n)
        self.inp: dict[str, str] = {}     # input name -> absolute ref (Inputs!$B$5 / row refs)
        self.calc: dict[str, dict[str, str]] = {s: {} for s in SCENARIOS}
        self.out: dict[str, str] = {}

    # small helpers -----------------------------------------------------------------------
    @staticmethod
    def _abs(sheet: str, col: str, row: int) -> str:
        q = f"'{sheet}'" if " " in sheet else sheet
        return f"{q}!${col}${row}"

    def _label(self, sh: Sheet, text: str, *, bold=False, fill=None):
        c = sh.ws.cell(row=sh.row, column=1, value=text)
        c.font = BOLD if bold else Font(name="Calibri")
        if fill:
            for col in range(1, 4 + self.n):
                sh.ws.cell(row=sh.row, column=col).fill = fill

    def _section(self, sh: Sheet, text: str):
        sh.row += 1
        self._label(sh, text, bold=True, fill=SECTION_FILL)
        sh.row += 1

    def _years_header(self, sh: Sheet, label="Fiscal year"):
        self._label(sh, label, bold=True)
        for i, col in enumerate(self.cols):
            c = sh.ws[f"{col}{sh.row}"]
            c.value = int(self.a["base_year"]) + 1 + i
            c.font, c.fill, c.alignment = HEAD_FONT, HEAD_FILL, Alignment(horizontal="center")
        b = sh.ws[f"B{sh.row}"]
        b.value, b.font, b.fill = f"FY{self.a['base_year']}A", HEAD_FONT, HEAD_FILL
        sh.row += 1

    def input_scalar(self, sh: Sheet, name: str, label: str, value, fmt="num", note_key=None):
        self._label(sh, label)
        c = sh.ws[f"B{sh.row}"]
        c.value, c.font, c.fill, c.number_format = value, BLUE, INPUT_FILL, FMT[fmt]
        note = self.notes.get(note_key or name)
        if note:
            sh.ws[f"{self.note_col}{sh.row}"] = note
        self.inp[name] = self._abs(sh.ws.title, "B", sh.row)
        sh.row += 1

    def input_vector(self, sh: Sheet, name: str, label: str, values, fmt="pct", note_key=None,
                     base=None):
        self._label(sh, label)
        if base is not None:
            c = sh.ws[f"B{sh.row}"]
            c.value, c.font, c.fill, c.number_format = base, BLUE, INPUT_FILL, FMT["usd_m"]
            self.inp[f"{name}.base"] = self._abs(sh.ws.title, "B", sh.row)
        for col, v in zip(self.cols, values, strict=True):
            c = sh.ws[f"{col}{sh.row}"]
            c.value, c.font, c.fill, c.number_format = v, BLUE, INPUT_FILL, FMT[fmt]
        note = self.notes.get(note_key or name)
        if note:
            sh.ws[f"{self.note_col}{sh.row}"] = note
        self.inp[name] = sh.row   # row number on Inputs; columns follow the years
        sh.row += 1

    def iv(self, name: str, i: int) -> str:
        """Absolute ref to year i (0-based) of an Inputs vector."""
        return f"Inputs!${self.cols[i]}${self.inp[name]}"

    def formula_row(self, sh: Sheet, scen: str, name: str, label: str, make, fmt="usd_m",
                    base: str | None = None, bold=False):
        """One calc row: `make(i, col)` returns the formula for year i."""
        self._label(sh, label, bold=bold)
        if base is not None:
            c = sh.ws[f"B{sh.row}"]
            c.value, c.font, c.number_format = base, GREEN if "!" in base else BLACK, FMT[fmt]
        for i, col in enumerate(self.cols):
            c = sh.ws[f"{col}{sh.row}"]
            f = make(i, col)
            c.value, c.number_format = f, FMT[fmt]
            c.font = GREEN if ("!" in f and f.count("!") == f.count("Inputs!")) and f.count("(") == 0 \
                and f.lstrip("=").startswith("Inputs!") else BLACK
            if bold:
                c.font = Font(name="Calibri", bold=True)
        self.calc[scen][name] = sh.row
        sh.row += 1

    def formula_cell(self, sh: Sheet, scen: str | None, name: str, label: str, f: str,
                     fmt="num", bold=False):
        self._label(sh, label, bold=bold)
        c = sh.ws[f"B{sh.row}"]
        c.value, c.number_format = f, FMT[fmt]
        c.font = Font(name="Calibri", bold=True) if bold else BLACK
        ref = self._abs(sh.ws.title, "B", sh.row)
        if scen:
            self.calc[scen][name] = ref
        else:
            self.out[name] = ref
        sh.row += 1
        return ref

    def cv(self, scen: str, name: str, i: int | None = None, col: str | None = None) -> str:
        """Ref to a calc row (year i, same sheet: relative) or a calc scalar."""
        v = self.calc[scen][name]
        if isinstance(v, str):
            return v
        c = col if col is not None else self.cols[i]
        return f"{c}{v}"

    def cx(self, scen: str, name: str, i: int) -> str:
        """Cross-sheet ref to year i of a calc row."""
        return f"'{SHEET[scen]}'!${self.cols[i]}${self.calc[scen][name]}"

    # sheets --------------------------------------------------------------------------------
    def build(self) -> Workbook:
        self.wb.active.title = "Outputs"
        self._inputs()
        for s in SCENARIOS:
            self._calc(s)
        self._outputs()
        for ws in self.wb.worksheets:
            ws.column_dimensions["A"].width = 38
            ws.column_dimensions["B"].width = 16
            for col in self.cols:
                ws.column_dimensions[col].width = 14
            ws.column_dimensions[self.note_col].width = 90
            ws.freeze_panes = "B2" if ws.title != "Outputs" else None
        return self.wb

    def _inputs(self):
        a = self.a
        ws = self.wb.create_sheet("Inputs")
        sh = Sheet(ws)
        ws["A1"], ws["A1"].font = f"{a['ticker']} valuation inputs", TITLE
        ws["A2"] = "Blue = input (edit these). Every calculation references this tab."
        sh.row = 4
        self._section(sh, "General")
        self.input_scalar(sh, "price", "Share price", float(a["price"]), "px")
        self.input_scalar(sh, "as_of", "Price date (as of)", date.fromisoformat(str(a["as_of"])), "date")
        self.input_scalar(sh, "horizon", "Horizon (months)", float(a.get("horizon_months", 15)), "int",
                          note_key="horizon_months")
        self.input_scalar(sh, "months", "Months per year", 12.0, "int")
        self.input_scalar(sh, "days", "Days per year (for date fractions)", 365.25, "num")
        self.input_scalar(sh, "shares", "Diluted shares", float(a["shares_diluted"]), "shares",
                          note_key="shares_diluted")
        self.input_scalar(sh, "net_debt", "Net debt", float(a["net_debt"]), "usd_m")
        self.input_scalar(sh, "dps", "Dividend per share (annual)", float(a.get("dividend_per_share", 0.0)),
                          "px", note_key="dividend_per_share")
        lst = a.get("listing") or {}
        self.input_scalar(sh, "spu", "Ordinary shares per traded unit", float(lst.get("shares_per_unit", 1.0)),
                          "num", note_key="shares_per_unit")
        self.input_scalar(sh, "fx", "Local currency per trading currency", float(lst.get("fx_local_per_trading", 1.0)),
                          "factor", note_key="fx_local_per_trading")

        self._section(sh, "Timing")
        self._years_header(sh)
        fye0 = date.fromisoformat(str(a["base_fiscal_year_end"]))
        self.input_vector(sh, "fy_end", "Fiscal year end", [erb_model._fy_end(fye0, i + 1) for i in range(self.n)],
                          "date")

        self._section(sh, "Revenue")
        self._years_header(sh, "Growth by segment (base in column B)")
        segs = a["revenue"].get("segments") or {"Total": {"base": a["revenue"]["base"],
                                                         "growth": a["revenue"]["growth"]}}
        self.segments = list(segs)
        for k, (seg, cfg) in enumerate(segs.items()):
            self.input_vector(sh, f"seg{k}", seg, _vec(cfg["growth"], self.n), "pct",
                              base=float(cfg["base"]), note_key=seg)

        m = a["margins"]
        self._section(sh, "Margins and cash flow")
        self._years_header(sh)
        self.input_vector(sh, "ebitda_margin", "EBITDA margin", _vec(m["ebitda_margin"], self.n))
        self.input_vector(sh, "capex_pct", "CapEx % revenue", _vec(m["capex_pct_rev"], self.n),
                          note_key="capex_pct_rev")
        self.input_vector(sh, "tax", "Tax rate", _vec(m["tax_rate"], self.n), note_key="tax_rate")
        self.input_vector(sh, "other_pct", "Other income % revenue", _vec(m.get("other_income_pct_rev", 0.0), self.n),
                          note_key="other_income_pct_rev")
        self.input_vector(sh, "share_change", "Share count change", _vec(m.get("share_change", 0.0), self.n))
        self.input_vector(sh, "nwc_pct", "Change in NWC per $ of revenue growth",
                          _vec(m.get("nwc_pct_delta_rev", 0.0), self.n), note_key="nwc_pct_delta_rev")
        self.da_mode = "ppe" if m.get("d_and_a") else "pct"
        if self.da_mode == "ppe":
            self.input_scalar(sh, "ppe_base", "Net PP&E (base year)", float(m["d_and_a"]["ppe_base"]), "usd_m",
                              note_key="d_and_a")
            self.input_scalar(sh, "life", "Useful life (years)", float(m["d_and_a"]["useful_life"]), "num")
        else:
            self.input_vector(sh, "da_pct", "D&A % revenue", _vec(m["d_and_a_pct_rev"], self.n),
                              note_key="d_and_a_pct_rev")

        w = a["wacc"]
        self._section(sh, "Cost of capital (CAPM)")
        self.input_scalar(sh, "rf", "Risk-free rate", float(w["risk_free"]), "pct", note_key="risk_free")
        self.input_scalar(sh, "beta", "Beta", float(w["beta"]), "num")
        self.input_scalar(sh, "erp", "Equity risk premium", float(w["erp"]), "pct")
        self.input_scalar(sh, "kd", "Pre-tax cost of debt", float(w["cost_of_debt_pretax"]), "pct",
                          note_key="cost_of_debt_pretax")
        self.input_scalar(sh, "debt_tax", "Tax rate for the debt shield", float(w.get("tax_rate", 0.21)), "pct")
        self.input_scalar(sh, "e_value", "Equity value (market cap)", float(w["equity_value"]), "usd_m",
                          note_key="equity_value")
        self.input_scalar(sh, "d_value", "Debt value", float(w["debt_value"]), "usd_m", note_key="debt_value")

        t = a["terminal"]
        self._section(sh, "Terminal value")
        self.method = t.get("method", "exit_multiple")
        self.input_scalar(sh, "exit_mult", "Exit EV/EBITDA", float(t.get("exit_ev_ebitda") or 0.0), "x",
                          note_key="exit_ev_ebitda")
        self.input_scalar(sh, "perp_g", "Perpetual growth", float(t.get("perpetual_growth", 0.025)), "pct",
                          note_key="perpetual_growth")
        self._label(sh, "Terminal method")
        ws[f"B{sh.row}"] = self.method
        ws[f"B{sh.row}"].font = BLUE
        self.inp["method"] = self._abs("Inputs", "B", sh.row)
        sh.row += 1

        mult = a.get("multiples") or {}
        self._section(sh, "Target multiples (applied to NTM metrics at the target date)")
        self.input_scalar(sh, "fpe", "Forward P/E", float(mult.get("forward_pe") or 0.0), "x",
                          note_key="forward_pe")
        self.input_scalar(sh, "fevx", "EV / EBITDA", float(mult.get("ev_ebitda") or 0.0), "x", note_key="ev_ebitda")
        metric = a.get("metric_multiple")
        self.has_metric = bool(metric and metric.get("multiple"))
        if self.has_metric:
            self._years_header(sh)
            self.input_vector(sh, "metric_ps", f"{metric.get('name', 'Metric')} per share",
                              _vec(metric["per_share"], self.n), "px")
            self.input_scalar(sh, "metric_mult", f"{metric.get('name', 'Metric')} multiple",
                              float(metric["multiple"]), "x")

        weights = a.get("weights") or {}
        self._section(sh, "Method weights (normalised over the methods that apply)")
        for key in ("dcf", "pe", "ev_ebitda", "metric"):
            self.input_scalar(sh, f"w_{key}", f"Weight: {key}", float(weights.get(key, 0.0)), "pct")

        self._section(sh, "Scenarios (deltas vs base)")
        ws.cell(row=sh.row, column=1, value="Scenario").font = BOLD
        for j, h in enumerate(("Growth delta", "Margin delta (by final year)", "Multiple delta", "WACC delta")):
            ws.cell(row=sh.row, column=2 + j, value=h).font = BOLD
        sh.row += 1
        self.paths = {}
        for s in SCENARIOS:
            sc = erb_model.scenario(self.a, s)
            ws.cell(row=sh.row, column=1, value=s.capitalize())
            for j, (key, val) in enumerate((("g", sc.growth_delta), ("m", sc.margin_delta),
                                            ("k", sc.multiple_delta), ("w", sc.wacc_delta))):
                c = ws.cell(row=sh.row, column=2 + j, value=float(val))
                c.font, c.fill, c.number_format = BLUE, INPUT_FILL, FMT["pct"]
                self.inp[f"{s}.{key}"] = self._abs("Inputs", get_column_letter(2 + j), sh.row)
            sh.row += 1
            if sc.growth_path is not None:
                self.paths[s] = True
                self.input_vector(sh, f"{s}.path", f"{s.capitalize()} growth path (replaces growth)",
                                  _vec(sc.growth_path, self.n))

        b = a.get("benchmark") or {}
        self._section(sh, "Rating benchmark")
        self.input_scalar(sh, "sp500", "S&P 500 expected annual return", float(b.get("sp500_expected_return", 0.08)),
                          "pct", note_key="sp500_expected_return")
        self.input_scalar(sh, "band", "Outperform / Underperform band", float(b.get("band", 0.05)), "pct")
        ws.cell(row=3, column=3 + self.n, value="Notes (reasons carried over from assumptions.yaml)").font = BOLD

    def _calc(self, s: str):
        ws = self.wb.create_sheet(SHEET[s])
        sh = Sheet(ws)
        ws["A1"], ws["A1"].font = f"{self.a['ticker']} {s} case: calculations", TITLE
        ws["A2"] = "All formulas. Inputs live on the Inputs tab."
        sh.row = 4
        n_last = self.cols[-1]
        cv = lambda name, i: self.cv(s, name, i)
        prev = lambda i: self.cols[i - 1] if i else "B"

        self._section(sh, "Scenario")
        g = self.formula_cell(sh, s, "g_delta", "Growth delta", f"={self.inp[f'{s}.g']}", "pct")
        md = self.formula_cell(sh, s, "m_delta", "Margin delta (by final year)", f"={self.inp[f'{s}.m']}", "pct")
        k = self.formula_cell(sh, s, "k", "Multiple factor", f"=1+{self.inp[f'{s}.k']}", "factor")
        wd = self.formula_cell(sh, s, "w_delta", "WACC delta", f"={self.inp[f'{s}.w']}", "pct")

        self._section(sh, "Projections")
        self._years_header(sh)
        self.formula_row(sh, s, "idx", "Projection year", lambda i, c: "=1" if i == 0 else f"={prev(i)}{self.calc[s].get('idx', sh.row)}+1",
                         "int")
        idx_row = self.calc[s]["idx"]
        for kk, seg in enumerate(self.segments):
            growth_src = f"{s}.path" if s in self.paths else f"seg{kk}"
            self.formula_row(sh, s, f"g{kk}", f"{seg}: growth",
                             lambda i, c, src=growth_src: f"={self.iv(src, i)}+{g}", "pct")
            base_ref = self.inp[f"seg{kk}.base"]
            self.formula_row(sh, s, f"rev{kk}", f"{seg}: revenue",
                             lambda i, c, kk=kk: f"={prev(i)}{sh.row}*(1+{cv(f'g{kk}', i)})",
                             base=f"={base_ref}")
        rev_parts = [f"rev{kk}" for kk in range(len(self.segments))]
        self.formula_row(sh, s, "revenue", "Revenue",
                         lambda i, c: "=" + "+".join(cv(r, i) for r in rev_parts),
                         base="=" + "+".join(f"B{self.calc[s][r]}" for r in rev_parts), bold=True)
        rev_row = self.calc[s]["revenue"]
        self.formula_row(sh, s, "rev_growth", "Revenue growth",
                         lambda i, c: f"={c}{rev_row}/{prev(i)}{rev_row}-1", "pct")
        self.formula_row(sh, s, "ramp", "Margin delta ramp",
                         lambda i, c: f"={c}{idx_row}/${n_last}${idx_row}", "pct")
        self.formula_row(sh, s, "ebitda_margin", "EBITDA margin",
                         lambda i, c: f"={self.iv('ebitda_margin', i)}+{md}*{cv('ramp', i)}", "pct")
        self.formula_row(sh, s, "ebitda", "EBITDA", lambda i, c: f"={cv('revenue', i)}*{cv('ebitda_margin', i)}")
        self.formula_row(sh, s, "capex", "CapEx", lambda i, c: f"={cv('revenue', i)}*{self.iv('capex_pct', i)}")
        if self.da_mode == "ppe":
            self.formula_row(sh, s, "ppe_open", "Net PP&E, opening",
                             lambda i, c: f"={self.inp['ppe_base']}" if i == 0 else f"={prev(i)}{sh.row + 2}")   # prior closing
            open_row = self.calc[s]["ppe_open"]
            self.formula_row(sh, s, "d_and_a", "D&A (opening PP&E + half of CapEx) / life",
                             lambda i, c: f"=({c}{open_row}+{cv('capex', i)}/2)/{self.inp['life']}")
            self.formula_row(sh, s, "ppe_close", "Net PP&E, closing",
                             lambda i, c: f"={c}{open_row}+{cv('capex', i)}-{cv('d_and_a', i)}")
        else:
            self.formula_row(sh, s, "d_and_a", "D&A", lambda i, c: f"={cv('revenue', i)}*{self.iv('da_pct', i)}")
        self.formula_row(sh, s, "ebit", "EBIT", lambda i, c: f"={cv('ebitda', i)}-{cv('d_and_a', i)}")
        self.formula_row(sh, s, "other", "Other income", lambda i, c: f"={cv('revenue', i)}*{self.iv('other_pct', i)}")
        self.formula_row(sh, s, "pretax", "Pre-tax income", lambda i, c: f"={cv('ebit', i)}+{cv('other', i)}")
        self.formula_row(sh, s, "net_income", "Net income", lambda i, c: f"={cv('pretax', i)}*(1-{self.iv('tax', i)})")
        self.formula_row(sh, s, "shares", "Diluted shares",
                         lambda i, c: f"={prev(i)}{sh.row}*(1+{self.iv('share_change', i)})", "shares",
                         base=f"={self.inp['shares']}")
        self.formula_row(sh, s, "eps", "EPS", lambda i, c: f"={cv('net_income', i)}/{cv('shares', i)}", "px")
        self.formula_row(sh, s, "d_nwc", "Change in NWC",
                         lambda i, c: f"=({c}{rev_row}-{prev(i)}{rev_row})*{self.iv('nwc_pct', i)}")
        self.formula_row(sh, s, "ufcf", "Unlevered free cash flow",
                         lambda i, c: (f"={cv('ebit', i)}*(1-{self.iv('tax', i)})+{cv('d_and_a', i)}"
                                       f"-{cv('capex', i)}-{cv('d_nwc', i)}"), bold=True)

        self._section(sh, "Cost of capital")
        inp = self.inp
        ke0 = self.formula_cell(sh, s, "ke0", "Cost of equity (CAPM)", f"={inp['rf']}+{inp['beta']}*{inp['erp']}", "pct")
        kd = self.formula_cell(sh, s, "kd_at", "After-tax cost of debt", f"={inp['kd']}*(1-{inp['debt_tax']})", "pct")
        we = self.formula_cell(sh, s, "we", "Equity weight",
                               f"=IF({inp['e_value']}+{inp['d_value']}=0,1,{inp['e_value']}/({inp['e_value']}+{inp['d_value']}))",
                               "pct")
        wacc = self.formula_cell(sh, s, "wacc", "WACC", f"={we}*{ke0}+(1-{we})*{kd}+{wd}", "pct", bold=True)
        ke = self.formula_cell(sh, s, "ke", "Cost of equity (scenario)", f"={ke0}+{wd}", "pct")

        self._section(sh, "DCF (stub year counted pro rata, mid-period discounting)")
        self._years_header(sh)
        self.formula_row(sh, s, "fy_end", "Fiscal year end", lambda i, c: f"={self.iv('fy_end', i)}", "date")
        self.formula_row(sh, s, "end_t", "Years from price date to year end",
                         lambda i, c: f"=({cv('fy_end', i)}-{inp['as_of']})/{inp['days']}", "factor")
        end_row = self.calc[s]["end_t"]
        self.formula_row(sh, s, "prev_end", "Previous counted year end",
                         lambda i, c: "=0" if i == 0 else f"=IF({prev(i)}{end_row}>0,{prev(i)}{end_row},0)", "factor")
        self.formula_row(sh, s, "start_t", "Period start",
                         lambda i, c: f"=MAX({cv('prev_end', i)},{c}{end_row}-1)", "factor")
        self.formula_row(sh, s, "frac", "Fraction of the year counted",
                         lambda i, c: f"=IF({c}{end_row}>0,{c}{end_row}-{cv('start_t', i)},0)", "factor")
        self.formula_row(sh, s, "t_mid", "Discount period (mid-point)",
                         lambda i, c: f"=({cv('start_t', i)}+{c}{end_row})/2", "factor")
        self.formula_row(sh, s, "disc", "Discount factor", lambda i, c: f"=(1+{wacc})^-{cv('t_mid', i)}", "factor")
        self.formula_row(sh, s, "pv", "PV of counted cash flow",
                         lambda i, c: f"={cv('ufcf', i)}*{cv('frac', i)}*{cv('disc', i)}")
        pv_row = self.calc[s]["pv"]
        pv_sum = self.formula_cell(sh, s, "pv_sum", "Sum of PV of cash flows",
                                   f"=SUM({self.cols[0]}{pv_row}:{n_last}{pv_row})", "usd_m")
        n_t = self.formula_cell(sh, s, "n_t", "Years to final year end",
                                f"=MAX({n_last}{end_row},0)", "factor")
        tv = self.formula_cell(
            sh, s, "tv", "Terminal value",
            f'=IF({inp["method"]}="exit_multiple",{inp["exit_mult"]}*{k}*{cv("ebitda", self.n - 1)},'
            f'{cv("ufcf", self.n - 1)}*(1+{inp["perp_g"]})/({wacc}-{inp["perp_g"]}))', "usd_m")
        pv_tv = self.formula_cell(sh, s, "pv_tv", "PV of terminal value", f"={tv}*(1+{wacc})^-{n_t}", "usd_m")
        ev = self.formula_cell(sh, s, "ev", "Enterprise value", f"={pv_sum}+{pv_tv}", "usd_m", bold=True)
        eq = self.formula_cell(sh, s, "equity", "Equity value", f"={ev}-{inp['net_debt']}", "usd_m")
        ps_local = self.formula_cell(sh, s, "ps_local", "Value per share today (local)", f"={eq}/{inp['shares']}", "px")
        listing = f"{inp['spu']}/{inp['fx']}"
        ps = self.formula_cell(sh, s, "ps", "Value per traded unit today", f"={ps_local}*{listing}", "px")
        self.formula_cell(sh, s, "tv_share", "Terminal value share of EV", f"={pv_tv}/{ev}", "pct")

        self._section(sh, "Methods at the target date")
        h = self.formula_cell(sh, s, "h", "Horizon (years)", f"={inp['horizon']}/{inp['months']}", "factor")
        # Python's round() (used by the engine) rounds an exact .5 to even; Excel's ROUND rounds
        # it away from zero. Reproduce the engine so both agree on the target date.
        x = f"{h}*{inp['days']}"
        target = self.formula_cell(
            sh, s, "target", "Target date (horizon rounded to whole days, half to even)",
            f"={inp['as_of']}+IF(({x}-INT({x}))*2=1,2*ROUND({x}/2,0),ROUND({x},0))", "date")
        self._years_header(sh)
        fy_row = self.calc[s]["fy_end"]

        def ntm_weight(i, c):
            end = f"{c}{fy_row}"
            first = f"{end}>={target}" if i == 0 else f"AND({end}>={target},{prev(i)}{fy_row}<{target})"
            if i + 1 >= self.n:   # the last year can't start an NTM window (needs a next year)
                own = "0"
            else:
                own = f"MIN(({end}-{target})/{inp['days']},1)"
            prev_part = "0"
            if i >= 1:
                p = f"{prev(i)}{fy_row}"
                pfirst = f"{p}>={target}" if i == 1 else f"AND({p}>={target},{self.cols[i - 2]}{fy_row}<{target})"
                prev_part = f"IF({pfirst},1-MIN(({p}-{target})/{inp['days']},1),0)"
            return f"=IF({first},{own},{prev_part})"

        self.formula_row(sh, s, "ntm_w", "NTM weight", ntm_weight, "factor")
        w_row = self.calc[s]["ntm_w"]
        dot = lambda row: "+".join(f"{c}{w_row}*{c}{row}" for c in self.cols)
        ntm_eps = self.formula_cell(sh, s, "ntm_eps", "NTM EPS at target", f"={dot(self.calc[s]['eps'])}", "px")
        ntm_ebitda = self.formula_cell(sh, s, "ntm_ebitda", "NTM EBITDA at target",
                                       f"={dot(self.calc[s]['ebitda'])}", "usd_m")

        res = self.results["results"][s]
        self.methods = [m for m in ("dcf", "pe", "ev_ebitda", "metric") if m in res["methods"]]
        refs = {}
        refs["dcf"] = self.formula_cell(sh, s, "m_dcf", "DCF (rolled to target date, less dividends)",
                                        f"={ps}*(1+{ke})^{h}-{inp['dps']}*{h}", "px")
        if "pe" in self.methods:
            refs["pe"] = self.formula_cell(sh, s, "m_pe", "P/E x NTM EPS",
                                           f"={inp['fpe']}*{k}*{ntm_eps}*{listing}", "px")
        if "ev_ebitda" in self.methods:
            refs["ev_ebitda"] = self.formula_cell(
                sh, s, "m_ev_ebitda", "EV/EBITDA x NTM EBITDA, less net debt",
                f"=({inp['fevx']}*{k}*{ntm_ebitda}-{inp['net_debt']})/{inp['shares']}*{listing}", "px")
        if "metric" in self.methods:
            ntm_metric = self.formula_cell(sh, s, "ntm_metric", "NTM metric per share",
                                           "=" + "+".join(f"{c}{w_row}*{self.iv('metric_ps', i)}"
                                                          for i, c in enumerate(self.cols)), "px")
            refs["metric"] = self.formula_cell(sh, s, "m_metric", "Metric multiple x NTM metric",
                                               f"={inp['metric_mult']}*{k}*{ntm_metric}*{listing}", "px")
        w_sum = "+".join(inp[f"w_{m}"] for m in self.methods)
        weighted = "+".join(f"{refs[m]}*{inp[f'w_{m}']}" for m in self.methods)
        pt = self.formula_cell(sh, s, "pt", "Price target (weighted)",
                               f"=IF({w_sum}=0,{refs['dcf']},({weighted})/({w_sum}))", "px", bold=True)
        for m in self.methods:
            self.formula_cell(sh, s, f"wt_{m}", f"Weight used: {m}",
                              f"=IF({w_sum}=0,IF(\"{m}\"=\"dcf\",1,0),{inp[f'w_{m}']}/({w_sum}))", "pct")
        pr = self.formula_cell(sh, s, "price_return", "Price return", f"={pt}/{inp['price']}-1", "pct")
        self.formula_cell(sh, s, "total_return", "Total return (incl. dividends)",
                          f"={pr}+{inp['dps']}*{h}/{inp['price']}", "pct", bold=True)
        self.formula_cell(sh, s, "ke_ref", "Cost of equity used for the roll-forward", f"={ke}", "pct")

    def _outputs(self):
        ws = self.wb["Outputs"]
        sh = Sheet(ws)
        a = self.a
        ws["A1"], ws["A1"].font = f"{a.get('ticker')} model v{self.version}: outputs", TITLE
        ws["A2"] = (f"Prepared by {self.prepared_by}. Draft until the Captain approves it; "
                    "approved versions are the firm's official numbers.")
        sh.row = 4
        ws.cell(row=sh.row, column=1, value="Scenario").font = BOLD
        heads = ["Price target", "Price return", "Total return", "DCF value today", "WACC", "TV share of EV"]
        for j, hd in enumerate(heads):
            c = ws.cell(row=sh.row, column=2 + j, value=hd)
            c.font, c.fill = HEAD_FONT, HEAD_FILL
        sh.row += 1
        fmts = ["px", "pct", "pct", "px", "pct", "pct"]
        keys = ["pt", "price_return", "total_return", "ps", "wacc", "tv_share"]
        for s in ("bull", "base", "bear"):
            ws.cell(row=sh.row, column=1, value=s.capitalize()).font = BOLD
            for j, (key, fmt) in enumerate(zip(keys, fmts, strict=True)):
                c = ws.cell(row=sh.row, column=2 + j, value=f"={self.calc[s][key]}")
                c.number_format, c.font = FMT[fmt], GREEN
                self.out[f"{s}.{key}"] = self._abs("Outputs", get_column_letter(2 + j), sh.row)
            sh.row += 1

        sh.row += 1
        self._label(sh, "Base case: method values", bold=True)
        sh.row += 1
        for m in self.methods:
            ws.cell(row=sh.row, column=1, value=m)
            c = ws.cell(row=sh.row, column=2, value=f"={self.calc['base'][f'm_{m}']}")
            c.number_format, c.font = FMT["px"], GREEN
            c2 = ws.cell(row=sh.row, column=3, value=f"={self.calc['base'][f'wt_{m}']}")
            c2.number_format, c2.font = FMT["pct"], GREEN
            self.out[f"method.{m}"] = self._abs("Outputs", "B", sh.row)
            sh.row += 1

        sh.row += 1
        self._label(sh, "Rating", bold=True)
        sh.row += 1
        inp = self.inp
        h = self.calc["base"]["h"]
        bench = self.formula_cell(sh, None, "bench", "S&P 500 expected return over the horizon",
                                  f"=(1+{inp['sp500']})^{h}-1", "pct")
        excess = self.formula_cell(sh, None, "excess", "Base-case excess return",
                                   f"={self.calc['base']['total_return']}-{bench}", "pct")
        self.formula_cell(sh, None, "rating", "Rating",
                          f'=IF({excess}>{inp["band"]},"Outperform",IF({excess}<-{inp["band"]},"Underperform","Neutral"))',
                          "num", bold=True)

        # Sensitivity: base-case DCF value at the target date, WACC x terminal driver.
        sh.row += 1
        exit_mode = self.method == "exit_multiple"
        self._label(sh, "Sensitivity: base-case DCF value at the target date", bold=True)
        sh.row += 1
        ws.cell(row=sh.row, column=1, value="Steps (inputs)").font = BOLD
        col_steps = [-2, -1, 0, 1, 2] if exit_mode else [-0.01, -0.005, 0.0, 0.005, 0.01]
        row_steps = [-0.01, -0.005, 0.0, 0.005, 0.01]
        step_row = sh.row
        for j, v in enumerate(col_steps):
            c = ws.cell(row=sh.row, column=3 + j, value=v)
            c.font, c.fill, c.number_format = BLUE, INPUT_FILL, FMT["num" if exit_mode else "pct"]
        sh.row += 1
        head_row = sh.row
        ws.cell(row=sh.row, column=1, value="WACC step (input) / WACC").font = BOLD
        ws.cell(row=sh.row, column=2, value="Exit EV/EBITDA" if exit_mode else "Perpetual growth").font = BOLD
        base_col_val = inp["exit_mult"] if exit_mode else inp["perp_g"]
        for j in range(5):
            col = get_column_letter(3 + j)
            c = ws.cell(row=sh.row, column=3 + j, value=f"={base_col_val}+{col}{step_row}")
            c.number_format, c.font = FMT["x" if exit_mode else "pct"], BOLD
        sh.row += 1
        b = self.calc["base"]
        cols = self.cols
        n = self.n
        self.sens_cells = []
        for i, dw in enumerate(row_steps):
            r = sh.row
            ws.cell(row=r, column=1, value=dw).font = BLUE
            ws.cell(row=r, column=1).number_format = FMT["pct"]
            ws.cell(row=r, column=1).fill = INPUT_FILL
            wcell = ws.cell(row=r, column=2, value=f"={b['wacc']}+A{r}")
            wcell.number_format, wcell.font = FMT["pct"], BOLD
            w = f"$B{r}"
            row_refs = []
            for j in range(5):
                col = get_column_letter(3 + j)
                driver = f"{col}${head_row}"
                pv = "+".join(f"{self.cx('base', 'ufcf', t)}*{self.cx('base', 'frac', t)}*(1+{w})^-{self.cx('base', 't_mid', t)}"
                              for t in range(n))
                if exit_mode:
                    tv = f"{driver}*{self.cx('base', 'ebitda', n - 1)}"
                else:
                    tv = f"{self.cx('base', 'ufcf', n - 1)}*(1+{driver})/({w}-{driver})"
                f = (f"=(({pv})+{tv}*(1+{w})^-{b['n_t']}-{inp['net_debt']})/{inp['shares']}"
                     f"*{inp['spu']}/{inp['fx']}*(1+{b['ke0']})^{b['h']}-{inp['dps']}*{b['h']}")
                c = ws.cell(row=r, column=3 + j, value=f)
                c.number_format = FMT["px"]
                row_refs.append(self._abs("Outputs", col, r))
            self.sens_cells.append(row_refs)
            sh.row += 1
        del cols
        ws.column_dimensions["A"].width = 38

    # ---- save ------------------------------------------------------------------------------
    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.build()
        self.wb.save(path)
        return path


# ---- check ------------------------------------------------------------------------------------
def expected_values(a: dict) -> dict[str, float | str]:
    """What erb's Python engine says, keyed like ModelWorkbook.out / calc refs."""
    out = erb_model.run(copy.deepcopy(a))
    exp: dict[str, float | str] = {}
    for s in SCENARIOS:
        r = out["results"][s]
        exp[f"{s}.pt"] = r["price_target"]
        exp[f"{s}.price_return"] = r["price_return"]
        exp[f"{s}.total_return"] = r["total_return"]
        exp[f"{s}.ps"] = erb_model._listing(a, r["dcf"]["value_per_share_local"])
        exp[f"{s}.wacc"] = r["wacc"]["wacc"]
        exp[f"{s}.tv_share"] = r["dcf"]["tv_share_of_ev"]
    for m, v in out["results"]["base"]["methods"].items():
        exp[f"method.{m}"] = v
    exp["rating"] = out["rating"]["rating"]
    exp["excess"] = out["rating"]["excess_return"]
    exp["sensitivity"] = out["sensitivity"]["values"]
    return exp


def check(path: Path, a: dict, rel_tol: float = 1e-6) -> dict:
    """Recompute every formula and compare with erb's engine. Also enforce the house rules:
    no hard-coded numbers in calculations, inputs in blue."""
    wb = load_workbook(path)   # formulas, not cached values
    ev = WorkbookEvaluator(wb)
    problems: list[str] = []
    try:
        n_formulas = ev.evaluate_all()
    except Exception as e:   # noqa: BLE001 - any evaluation failure is a failed check
        return {"ok": False, "problems": [f"Formula error: {e}"], "formulas": 0, "compared": 0}

    mw = ModelWorkbook(a)   # rebuild the layout in memory to know which cell is which
    mw.build()
    exp = expected_values(a)
    compared = 0
    for key, ref in mw.out.items():
        if key not in exp:
            continue
        got, want = ev.cell(ref), exp[key]
        compared += 1
        if isinstance(want, str):
            if got != want:
                problems.append(f"{key}: workbook says {got!r}, engine says {want!r}")
        elif want is not None and abs(got - want) > rel_tol * max(1.0, abs(want)):
            problems.append(f"{key}: workbook {got:,.6f} vs engine {want:,.6f}")
    for i, row in enumerate(mw.sens_cells):
        for j, ref in enumerate(row):
            got, want = ev.cell(ref), exp["sensitivity"][i][j]
            compared += 1
            if abs(got - want) > rel_tol * max(1.0, abs(want)):
                problems.append(f"sensitivity[{i}][{j}]: workbook {got:,.4f} vs engine {want:,.4f}")

    for ws in wb.worksheets:
        if ws.title in ("Inputs",):
            continue
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    lits = hardcoded_numbers(c.value)
                    if lits:
                        problems.append(f"{ws.title}!{c.coordinate} has hard-coded {', '.join(lits)}")
    return {"ok": not problems, "problems": problems[:30], "formulas": n_formulas,
            "compared": compared}


def build_model(assumptions_path: Path, out_path: Path, *, version: int,
                prepared_by: str = "Quant Department") -> dict:
    """Build the workbook and check it. Returns the check result plus headline outputs."""
    a, notes = load_assumptions(assumptions_path)
    mw = ModelWorkbook(a, notes, version=version, prepared_by=prepared_by)
    mw.save(out_path)
    result = check(out_path, a)
    res = mw.results["results"]
    result.update({
        "path": str(out_path),
        "price_targets": {s: round(res[s]["price_target"], 2) for s in ("bear", "base", "bull")},
        "total_returns": {s: round(res[s]["total_return"], 4) for s in ("bear", "base", "bull")},
        "rating": mw.results["rating"]["rating"],
        "price": float(a["price"]),
        "warnings": mw.results["warnings"],
        "as_of": str(a["as_of"]),
        "target_date": str(date.fromisoformat(str(a["as_of"])) + timedelta(
            days=round(float(a.get("horizon_months", 15)) / 12 * 365.25))),
    })
    return result
