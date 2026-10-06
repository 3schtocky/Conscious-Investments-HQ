// The phone's Portfolio. The contest comes first: our paper portfolio against the S&P 500, as two
// numbers over one chart. Drag a finger along the chart and the numbers follow it, like a markets
// app. Positions are quiet rows, each with a small entry-to-target track. Same data as desktop.
import { h, plain } from "./dom";
import { tap } from "./haptics";
import { gapSentence, indexReturn, shortDate, trackPoints, vsLine } from "./portfolioModel";
import type { PortfolioView } from "./portfolio";

type Row = PortfolioView["open"][number];
const SVG = "http://www.w3.org/2000/svg";
const el = (tag: string, attrs: Record<string, string | number> = {}) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
};
const usd = (x: number | null | undefined, digits = 2) =>
  x === null || x === undefined ? "n/a" : `$${x.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const tone = (x: number | null | undefined) => (x === null || x === undefined ? "" : x >= 0 ? "up" : "down");
const signed = (x: number | null | undefined) => (x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)}%`);

let scrubbing = false;
/** A re-render in the middle of a drag would snatch the chart from under the finger. */
export const isScrubbing = () => scrubbing;

export function renderMobilePortfolio(root: HTMLElement, v: PortfolioView, o: { captain: string }) {
  root.replaceChildren();
  if (v.return !== null && !v.priced) {
    root.append(h("div", { class: "pf-hero" }, h("p", { class: "pf-lede" }, "Prices are unavailable right now."),
      h("p", { class: "pf-small" }, "The scoreboard is hidden until quotes are back, so no false return is shown. Positions below are at their entry price.")));
  } else if (v.return === null || v.history.length < 2) {
    root.append(h("div", { class: "pf-hero" },
      h("p", { class: "pf-lede" }, `A ${usd(v.starting_capital, 0)} paper portfolio, waiting for its first position.`),
      h("p", { class: "pf-small" }, `The comparison with the S&P 500 starts once ${o.captain} approves one. No real money.`)));
  } else {
    root.append(hero(v));
  }

  const open = v.open.map((r) => position(r, true));
  root.append(h("h2", { class: "pf-h" }, "Positions"),
    open.length ? h("div", { class: "pf-list" }, ...open)
      : h("p", { class: "pf-empty" }, `No positions yet. A lead proposes one once a model ${o.captain} approved rates a name Outperform.`));
  if (v.closed.length) root.append(h("h2", { class: "pf-h" }, "Closed"), h("div", { class: "pf-list" }, ...v.closed.map((r) => position(r, false))));
  root.append(h("p", { class: "pf-foot" }, `Paper portfolio: no real money. Positions enter or leave only when ${o.captain} approves.${v.note ? ` ${v.note}` : ""}`));
}

// ---- the race --------------------------------------------------------------------------------------
function hero(v: PortfolioView): HTMLElement {
  const data = v.history;
  const last = data[data.length - 1];
  const usNum = h("div", { class: "pf-num us" }), themNum = h("div", { class: "pf-num" }), gap = h("div", { class: "pf-gap" });
  const show = (i: number | null) => {   // null = the live figures; otherwise the day under the finger
    if (i === null) {
      usNum.textContent = signed(v.return); usNum.className = `pf-num us ${tone(v.return)}`;
      themNum.textContent = signed(v.benchmark_return);
      gap.textContent = gapSentence(v.vs_benchmark === null ? null : v.vs_benchmark * 100, v.since);
    } else {
      const d = data[i];
      usNum.textContent = signed(indexReturn(d.portfolio)); usNum.className = `pf-num us ${tone(d.portfolio - 100)}`;
      themNum.textContent = signed(indexReturn(d.benchmark));
      const g = gapSentence(d.portfolio - d.benchmark, null);
      gap.textContent = `${shortDate(d.day)}: ${g[0].toLowerCase()}${g.slice(1)}`;
    }
  };
  show(null);

  const W = Math.max(280, Math.round(document.querySelector(".panel")?.clientWidth || 361)), H = 212, m = { l: 18, r: 18, t: 16, b: 26 };
  const all = data.flatMap((d) => [d.portfolio, d.benchmark]);
  let lo = Math.min(...all, 100), hi = Math.max(...all, 100);
  const pad = Math.max((hi - lo) * 0.14, 0.6); lo -= pad; hi += pad;
  const x = (i: number) => m.l + (i / (data.length - 1)) * (W - m.l - m.r);
  const y = (val: number) => m.t + (1 - (val - lo) / (hi - lo)) * (H - m.t - m.b);
  const line = (key: "portfolio" | "benchmark") => data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join("");
  const between = `${line("portfolio")}${[...data].reverse().map((d, k) => `L${x(data.length - 1 - k).toFixed(1)},${y(d.benchmark).toFixed(1)}`).join("")}Z`;
  const ahead = last.portfolio >= last.benchmark;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "pf-chart", role: "img",
    "aria-label": `Paper portfolio against the S&P 500 since ${shortDate(data[0].day)}, both starting at 100. Drag to read any day.` }) as SVGSVGElement;
  svg.append(el("line", { x1: m.l, x2: W - m.r, y1: y(100), y2: y(100), class: "pf-base" }),
    el("path", { d: between, class: `pf-between ${ahead ? "up" : "down"}` }),
    el("path", { d: line("benchmark"), class: "pf-line them" }), el("path", { d: line("portfolio"), class: "pf-line us" }),
    el("circle", { cx: x(data.length - 1), cy: y(last.benchmark), r: 3.5, class: "pf-dot them" }),
    el("circle", { cx: x(data.length - 1), cy: y(last.portfolio), r: 5, class: "pf-dot us" }));
  for (const [i, anchor] of [[0, "start"], [data.length - 1, "end"]] as [number, string][]) {
    const t = el("text", { x: x(i), y: H - 7, class: "pf-axis", "text-anchor": anchor });
    t.textContent = shortDate(data[i].day);
    svg.append(t);
  }
  const cross = el("line", { y1: m.t, y2: H - m.b, class: "pf-cross", visibility: "hidden" });
  const cu = el("circle", { r: 5, class: "pf-dot us", visibility: "hidden" }), ct = el("circle", { r: 3.5, class: "pf-dot them", visibility: "hidden" });
  svg.append(cross, ct, cu);

  let lastI = -1, lastTick = 0;
  const at = (e: PointerEvent) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    return Math.max(0, Math.min(data.length - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (data.length - 1))));
  };
  const move = (e: PointerEvent) => {
    const i = at(e);
    if (i === lastI) return;
    lastI = i;
    cross.setAttribute("x1", String(x(i))); cross.setAttribute("x2", String(x(i)));
    cu.setAttribute("cx", String(x(i))); cu.setAttribute("cy", String(y(data[i].portfolio)));
    ct.setAttribute("cx", String(x(i))); ct.setAttribute("cy", String(y(data[i].benchmark)));
    for (const n of [cross, cu, ct]) n.setAttribute("visibility", "visible");
    show(i);
    if (performance.now() - lastTick > 90) { lastTick = performance.now(); tap(); }
  };
  const end = () => {
    scrubbing = false; lastI = -1;
    for (const n of [cross, cu, ct]) n.setAttribute("visibility", "hidden");
    show(null);
  };
  svg.addEventListener("pointerdown", (e) => { scrubbing = true; svg.setPointerCapture(e.pointerId); move(e); });
  svg.addEventListener("pointermove", (e) => { if (scrubbing) move(e); });
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);

  const invested = v.value ? Math.max(0, Math.min(1, v.invested / v.value)) : 0;
  return h("section", { class: "pf-hero" },
    h("div", { class: "pf-race" },
      h("div", { class: "pf-side" }, usNum, h("div", { class: "pf-cap" }, h("i", { class: "sw us" }), "Our portfolio")),
      h("div", { class: "pf-side" }, themNum, h("div", { class: "pf-cap" }, h("i", { class: "sw them" }), "S&P 500"))),
    gap, svg as unknown as Node,
    h("div", { class: "pf-alloc" },
      h("div", { class: "pf-bar", role: "img", "aria-label": `${Math.round(invested * 100)} percent invested` }, h("span", { style: `width:${invested * 100}%` })),
      h("div", { class: "pf-small" }, `Paper money, no real trades. ${Math.round(invested * 100)}% invested, ${Math.round((1 - invested) * 100)}% cash.${v.picks_judged ? ` ${v.picks_beating} of ${v.picks_judged} positions are ahead of the S&P 500.` : ""}`)));
}

// ---- one position ---------------------------------------------------------------------------------
/** "Now $131.90, 16.8% to go to the target." / "...3.1% past the target." */
function targetLine(price: number | null, toTarget: number | null): string | null {
  if (price === null || toTarget === null) return null;
  const x = Math.abs(toTarget * 100).toFixed(1);
  return `Now ${usd(price)}, ${toTarget >= 0 ? `${x}% to go to the target.` : `${x}% past the target.`}`;
}

function position(r: Row, isOpen: boolean): HTMLElement {
  const t = trackPoints(r.entry_price, r.price ?? r.entry_price, isOpen ? r.base_target : null);
  const up = (r.price ?? r.entry_price) >= r.entry_price;
  const from = Math.min(t.entry, t.now) * 100, to = Math.max(t.entry, t.now) * 100;
  const why = h("div", { class: "pf-why" },
    h("p", {}, plain(r.thesis)),
    h("p", { class: "pf-small" }, `Proposed by ${r.proposed_by_name}.${!isOpen && r.exit_reason ? ` Closed because: ${r.exit_reason}` : ""}`));
  const btn = h("button", { class: "pf-pos-btn", "aria-expanded": "false", onclick: () => {
    tap();
    const on = row.classList.toggle("open");
    btn.setAttribute("aria-expanded", String(on));
  } },
    h("div", { class: "pf-pos-top" },
      h("span", { class: "pf-tkr" }, r.ticker),
      isOpen && r.rating ? h("span", { class: `chip${r.rating === "Outperform" ? " good" : " warn"}` }, r.rating) : h("span", { class: "chip" }, `Closed ${shortDate(r.exit_day ?? "")}`),
      h("span", { class: `pf-ret ${tone(r.return)}` }, signed(r.return))),
    h("div", { class: "pf-track", "aria-hidden": "true" },
      h("span", { class: `pf-fill ${up ? "up" : "down"}`, style: `left:${from}%;width:${Math.max(to - from, 0.6)}%` }),
      h("span", { class: "pf-tick entry", style: `left:${t.entry * 100}%` }),
      t.target !== null ? h("span", { class: "pf-tick target", style: `left:${t.target * 100}%` }) : null,
      h("span", { class: `pf-now ${up ? "up" : "down"}`, style: `left:${t.now * 100}%` })),
    h("div", { class: "pf-ends" },
      h("span", {}, `Entered ${shortDate(r.entry_day)} at ${usd(r.entry_price)}`),
      isOpen ? h("span", {}, r.base_target ? `Target ${usd(r.base_target)}` : `Now ${usd(r.price)}`) : h("span", {}, `Out at ${usd(r.price)}`)),
    h("div", { class: "pf-sub" },
      [isOpen ? `${r.size_pct}% of the portfolio, ${usd(r.value, 0)}.` : null, vsLine(r.vs_spy) ? `${vsLine(r.vs_spy)}.` : null,
        isOpen && r.base_target ? targetLine(r.price, r.to_target) : null].filter(Boolean).join(" ")),
    h("span", { class: "pf-more" }, "Why we hold it"));
  const row = h("article", { class: `pf-pos${isOpen ? "" : " closed"}` }, btn, why);
  return row;
}
