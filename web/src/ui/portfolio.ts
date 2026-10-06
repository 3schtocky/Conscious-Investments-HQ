// Portfolio: the paper portfolio and its scoreboard against the S&P 500. No real money.
import type { OfficeState } from "../state";
import { clear, h, timeAgo } from "./dom";
import { isScrubbing, renderMobilePortfolio } from "./portfolioMobile";

interface Row {
  id: number; ticker: string; size_pct: number; entry_price: number; entry_day: string; exit_day: string | null;
  price: number | null; value: number | null; return: number | null; vs_spy: number | null; weight?: number | null;
  base_target: number | null; rating: string | null; to_target: number | null; thesis: string;
  proposed_by_name: string; exit_reason: string | null; entry_ts: number; exit_ts: number | null;
}
interface Point { day: string; portfolio: number; benchmark: number; value: number }
export interface PortfolioView {
  benchmark: string; starting_capital: number; value: number; cash: number; invested: number; since: string | null;
  return: number | null; benchmark_return: number | null; vs_benchmark: number | null;
  picks_beating: number; picks_judged: number; open: Row[]; closed: Row[]; history: Point[];
  pending: { id: number; title: string; action: string; ticker: string }[]; note: string; priced: boolean; sizes: number[];
}

export const pct = (x: number | null | undefined, digits = 1) =>
  x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`;
const usd = (x: number | null | undefined, digits = 2) =>
  x === null || x === undefined ? "n/a" : `$${x.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const tone = (x: number | null | undefined) => (x === null || x === undefined ? "" : x >= 0 ? "up" : "down");
const SVG = "http://www.w3.org/2000/svg";
const el = (tag: string, attrs: Record<string, string | number>) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
};

export class PortfolioPanel {
  view: PortfolioView | null = null;
  private loadedAt = 0;
  private inFlight = false;
  private again = false;
  /** A visitor on the public site: the sanitized view (no cards from the Captain's desk). */
  readOnly = false;
  /** The recorded Micron demo adds its own position card here while a visitor plays it (ui/demoTour.ts). */
  extra: (() => HTMLElement | null) | null = null;

  constructor(private root: HTMLElement, private state: OfficeState, private onChange: () => void,
              private openApprovals: () => void) {}

  async refresh() {
    if (this.inFlight) { this.again = true; return; }
    this.inFlight = true;
    this.loadedAt = Date.now();
    try {
      const res = await fetch(this.readOnly ? "/api/public/portfolio" : "/api/portfolio");
      if (res.ok) { this.view = await res.json(); this.onChange(); }
    } finally {
      this.inFlight = false;
      if (this.again) { this.again = false; this.refresh(); }
    }
  }

  /** Prices move: refresh while the tab is open (the server caches quotes for ten minutes). */
  maybeRefresh() { if (Date.now() - this.loadedAt > 120_000) this.refresh(); }

  render() {
    const top = this.root.scrollTop;
    const captain = this.state.captain.nickname;
    const v = this.view;
    // A phone gets its own layout (portfolioMobile.ts), except while the recorded demo shows its own position card.
    const phone = document.body.classList.contains("mobile") && !this.state.touring;
    this.root.classList.toggle("pf", phone);
    if (phone) {
      if (isScrubbing()) return;   // a finger is on the chart: leave it alone
      if (!v) { clear(this.root); this.root.append(h("p", { class: "empty" }, "Loading…")); return; }
      renderMobilePortfolio(this.root, v, { captain });
      this.root.scrollTop = top;
      return;
    }
    clear(this.root);
    if (this.state.touring) {
      this.root.append(h("p", { class: "muted small audit-intro" },
        `A paper portfolio: no real money, $100,000 to start. Normally the team sizes a position at 3, 5 or 8 percent. In this demo ${captain} chose 10 percent, so it is shown as an explicit decision and not a standard size.`));
    } else this.root.append(h("p", { class: "muted small audit-intro" },
      `A paper portfolio: no real money. Only names with an approved Outperform model can be proposed, at 3, 5 or 8 percent, and nothing enters or leaves until ${captain} approves the card.`));
    if (this.state.touring) {   // the demo shows only its own story: no other positions on this page
      const demo = this.extra?.();
      this.root.append(demo ?? h("p", { class: "empty" }, "No positions yet. The team is still working on Micron: watch for the moment the Captain approves a position."));
      this.root.scrollTop = top;
      return;
    }
    if (!v) { this.root.append(h("p", { class: "empty" }, "Loading…")); return; }

    this.root.append(this.scoreEl(v));
    for (const p of v.pending) {
      this.root.append(h("div", { class: "audit-card memory s-pending" },
        h("div", { class: "approval-head" }, h("span", { class: "chip warn" }, "Waiting for you"),
          h("span", { class: "chip ticker" }, p.ticker)),
        h("div", { class: "audit-subject" }, p.title),
        h("div", { class: "row" }, h("button", { class: "btn ghost small-btn", onclick: () => this.openApprovals() }, "Decide in Approvals"))));
    }
    if (v.history.length >= 2) this.root.append(h("h4", { class: "section" }, "Since the first position"), this.chartEl(v));
    this.root.append(h("h4", { class: "section" }, `Open positions (${v.open.length})`));
    if (!v.open.length) this.root.append(h("p", { class: "empty" }, "No positions yet. A lead proposes one once a model you approved rates a name Outperform."));
    for (const r of v.open) this.root.append(this.rowEl(r, v));
    if (v.closed.length) {
      this.root.append(h("h4", { class: "section" }, "Closed"));
      for (const r of v.closed) this.root.append(this.rowEl(r, v));
    }
    this.root.scrollTop = top;
  }

  private scoreEl(v: PortfolioView): HTMLElement {
    const tile = (label: string, value: string, cls = "") =>
      h("div", {}, h("span", { class: "muted small" }, label), h("strong", { class: cls }, value));
    const scored = v.return !== null && v.priced;
    if (v.return !== null && !v.priced) {
      return h("div", { class: "audit-card digest" },
        h("div", { class: "audit-subject" }, "Prices are unavailable right now"),
        h("div", { class: "muted small" }, "The scoreboard is hidden until quotes are back, so no false return is shown. Positions below are listed at their entry price."));
    }
    return h("div", { class: "audit-card digest" },
      h("div", { class: "score-hero" },
        h("div", {}, h("span", { class: "muted small" }, "Portfolio value"), h("div", { class: "hero-number" }, usd(v.value))),
        scored ? h("div", { class: "score-vs" },
          h("span", { class: "muted small" }, `vs S&P 500 since ${v.since}`),
          h("div", { class: `hero-delta ${tone(v.vs_benchmark)}` }, v.vs_benchmark === null ? "n/a" : `${pct(v.vs_benchmark)} pts`.replace("% pts", " pts"))) : null),
      scored ? h("div", { class: "scorecard" },
        tile("Portfolio", pct(v.return), tone(v.return)),
        tile(`S&P 500 (${v.benchmark})`, pct(v.benchmark_return)),
        tile("In cash", `${((v.cash / v.value) * 100).toFixed(0)}%`),
        tile("Picks ahead", v.picks_judged ? `${v.picks_beating} of ${v.picks_judged}` : "n/a"))
        : h("div", { class: "muted small" }, `Starts at ${usd(v.starting_capital, 0)}. The scoreboard opens with the first position.`),
      scored ? h("div", { class: "muted small" }, v.note) : null);
  }

  private rowEl(r: Row, v: PortfolioView): HTMLElement {
    const open = r.exit_day === null;
    const flagged = v.pending.some((p) => p.action === "exit" && p.ticker === r.ticker);
    return h("div", { class: `watch ${open ? "s-open" : "s-closed"}` },
      h("div", { class: "watch-head" },
        h("span", { class: "watch-ticker" }, r.ticker),
        h("span", { class: "chip" }, `${r.size_pct}% size`),
        open && r.rating ? h("span", { class: `chip${r.rating === "Outperform" ? " good" : " warn"}` }, r.rating) : null,
        flagged ? h("span", { class: "chip warn" }, "Exit card waiting") : null,
        !open ? h("span", { class: "chip" }, `Closed ${r.exit_day}`) : null,
        h("span", { class: "feed-time" }, timeAgo(r.entry_ts))),
      h("div", { class: "scorecard" },
        h("div", {}, h("span", { class: "muted small" }, `Entered ${r.entry_day.slice(5)}`), h("strong", {}, usd(r.entry_price))),
        h("div", {}, h("span", { class: "muted small" }, open ? "Now" : "Exit"), h("strong", {}, usd(r.price))),
        h("div", {}, h("span", { class: "muted small" }, "Return"), h("strong", { class: tone(r.return) }, pct(r.return))),
        h("div", {}, h("span", { class: "muted small" }, "vs S&P 500"), h("strong", { class: tone(r.vs_spy) }, pct(r.vs_spy)))),
      open ? h("div", { class: "muted small" },
        `${usd(r.value)} · ${r.weight === null || r.weight === undefined ? "n/a" : (r.weight * 100).toFixed(1) + "%"} of the portfolio`
        + (r.base_target ? ` · base target ${usd(r.base_target)} (${pct(r.to_target)} away)` : ""))
        : h("div", { class: "muted small" }, r.exit_reason ? `Why: ${r.exit_reason}` : ""),
      h("details", { class: "digest-past" }, h("summary", {}, `Thesis · proposed by ${r.proposed_by_name}`),
        h("p", { class: "watch-thesis" }, r.thesis)));
  }

  /** Portfolio and benchmark indexed to 100 at the first mark: one axis, directly comparable. */
  private chartEl(v: PortfolioView): HTMLElement {
    const data = v.history, W = 360, H = 170, m = { l: 34, r: 54, t: 10, b: 22 };
    const all = data.flatMap((d) => [d.portfolio, d.benchmark]);
    let lo = Math.min(...all, 100), hi = Math.max(...all, 100);
    const pad = Math.max((hi - lo) * 0.12, 0.5); lo -= pad; hi += pad;
    const x = (i: number) => m.l + (i / (data.length - 1)) * (W - m.l - m.r);
    const y = (val: number) => m.t + (1 - (val - lo) / (hi - lo)) * (H - m.t - m.b);
    const last = data[data.length - 1];
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img",
      "aria-label": `Paper portfolio and ${v.benchmark}, indexed to 100 on ${data[0].day}` }) as SVGSVGElement;
    const ticks = [lo + pad, 100, hi - pad].filter((t, i, a) => a.indexOf(t) === i && Math.abs(t - 100) > 0.05 || t === 100);
    for (const t of ticks) {
      svg.append(el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), class: t === 100 ? "grid base" : "grid" }));
      const label = el("text", { x: m.l - 6, y: y(t) + 3, class: "axis", "text-anchor": "end" });
      label.textContent = t.toFixed(t === 100 ? 0 : 1);
      svg.append(label);
    }
    const ends = (data[0].day === last.day ? [[data.length - 1, "end"]] : [[0, "start"], [data.length - 1, "end"]]) as [number, string][];
    for (const [i, anchor] of ends) {
      const label = el("text", { x: x(i), y: H - 6, class: "axis", "text-anchor": anchor });
      label.textContent = data[i].day.slice(5);
      svg.append(label);
    }
    const series = [{ key: "benchmark" as const, name: v.benchmark, cls: "s2" }, { key: "portfolio" as const, name: "Portfolio", cls: "s1" }];
    // Direct labels at the line ends; nudge them apart when the two series finish close together.
    let ly = series.map((s) => y(last[s.key]) + 3);
    if (Math.abs(ly[0] - ly[1]) < 11) { const mid = (ly[0] + ly[1]) / 2, up = ly[1] <= ly[0]; ly = [mid + (up ? 6 : -6), mid + (up ? -6 : 6)]; }
    series.forEach((s, i) => {
      svg.append(el("path", { d: data.map((d, j) => `${j ? "L" : "M"}${x(j).toFixed(1)},${y(d[s.key]).toFixed(1)}`).join(""), class: `line ${s.cls}` }));
      svg.append(el("circle", { cx: x(data.length - 1), cy: y(last[s.key]), r: 4, class: `dot ${s.cls}` }));
      const label = el("text", { x: W - m.r + 8, y: ly[i], class: "axis strong" });
      label.textContent = s.name;
      svg.append(label);
    });
    const cross = el("line", { y1: m.t, y2: H - m.b, class: "cross", visibility: "hidden" });
    svg.append(cross);
    const tip = h("div", { class: "chart-tip hidden" });
    const wrap = h("div", { class: "chart-wrap" });
    const move = (e: PointerEvent) => {
      const box = svg.getBoundingClientRect();
      const px = ((e.clientX - box.left) / box.width) * W;
      const i = Math.max(0, Math.min(data.length - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (data.length - 1))));
      const d = data[i];
      cross.setAttribute("x1", String(x(i))); cross.setAttribute("x2", String(x(i))); cross.setAttribute("visibility", "visible");
      clear(tip);
      tip.append(h("strong", {}, d.day),
        h("div", {}, h("span", { class: "key s1" }), `Portfolio ${d.portfolio.toFixed(1)} (${usd(d.value, 0)})`),
        h("div", {}, h("span", { class: "key s2" }), `${v.benchmark} ${d.benchmark.toFixed(1)}`));
      tip.classList.remove("hidden");
      tip.style.left = `${Math.min(Math.max((x(i) / W) * 100, 22), 70)}%`;
    };
    svg.addEventListener("pointermove", move as EventListener);
    svg.addEventListener("pointerleave", () => { tip.classList.add("hidden"); cross.setAttribute("visibility", "hidden"); });
    const legend = h("div", { class: "chart-legend small" },
      h("span", {}, h("span", { class: "key s1" }), "Portfolio"), h("span", {}, h("span", { class: "key s2" }), `S&P 500 (${v.benchmark})`),
      h("span", { class: "muted" }, `Both start at 100 on ${data[0].day}`));
    const table = h("details", { class: "digest-past" }, h("summary", {}, "Show the numbers"),
      h("table", { class: "chart-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Day"), h("th", {}, "Portfolio"), h("th", {}, v.benchmark), h("th", {}, "Value"))),
        h("tbody", {}, ...data.slice(-30).reverse().map((d) => h("tr", {}, h("td", {}, d.day), h("td", {}, d.portfolio.toFixed(1)),
          h("td", {}, d.benchmark.toFixed(1)), h("td", {}, usd(d.value, 0)))))));
    wrap.append(svg as unknown as Node, tip);
    return h("div", { class: "audit-card digest" }, legend, wrap, table);
  }
}
