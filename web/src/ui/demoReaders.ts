// The Micron demo's files as full-screen reader pages on a phone: a files list, the report with a
// section picker, the model as stacked Bear / Base / Bull cards, the client note with copyable
// posts, and the open position. Same data as the desktop panel (demoTour.ts), laid out for a thumb.
import { h } from "./dom";
import { pct, ratingTone, usd } from "./demoFormat";
import { tap } from "./haptics";
import type { ModelData, NoteData, ReportData } from "./demoTour";

export type FileView = "home" | "report" | "model" | "note" | "position";

export interface ReaderCtx {
  base: string;
  report: ReportData; model: ModelData; note: NoteData;
  section: number; setSection: (i: number) => void;
  have: Set<string>; fresh: Set<string>;
  open: (v: FileView) => void;
  positionCard: () => HTMLElement | null;
  run: { playing: boolean; finished: boolean; started: boolean; clock: string };
  startDemo: () => void; backToFloor: () => void;
  banner: string;
}

/** The iOS share sheet where there is one, otherwise just open the file. */
export function share(url: string, title: string) {
  const abs = new URL(url, location.href).href;
  if (typeof navigator.share === "function") void navigator.share({ title, url: abs }).catch(() => { /* dismissed */ });
  else window.open(abs, "_blank", "noopener");
}

const chip = (text: string, tone = "") => h("span", { class: `chip ${tone}`.trim() }, text);
const note = (banner: string) => h("p", { class: "mr-note" }, banner);

// ---- the files list --------------------------------------------------------------------------------
export function renderMobileFiles(c: ReaderCtx): HTMLElement[] {
  const card = (key: "report" | "model" | "note" | "position", title: string, blurb: string, meta: string) => {
    const ready = c.have.has(key);
    return h("button", { class: `m-file${ready ? "" : " locked"}`, disabled: !ready, onclick: () => { tap(); c.open(key); } },
      h("span", { class: "m-file-main" },
        h("span", { class: "m-file-title" }, title),
        h("span", { class: "m-file-blurb" }, ready ? blurb : "The office has not made this yet. Keep watching."),
        ready ? h("span", { class: "m-file-meta" }, meta) : null),
      ready ? (c.fresh.has(key) ? chip("New", "good") : chip("Ready")) : chip("Not yet"),
      h("span", { class: "m-row-chev", "aria-hidden": "true" }, "›"));
  };
  const r = c.run;
  return [
    h("div", { class: "m-card mr-intro" },
      h("div", { class: "m-card-label" }, "Micron (MU) demo"),
      h("div", { class: "m-card-body" }, r.playing ? `The demo is playing (${r.clock}). Files appear here as the office makes them.`
        : r.started ? "These are the files from your run. Watch again to see how the office made them."
        : "A five-minute recorded run: the office makes a report, a model and a client note, then sizes a paper position."),
      r.playing
        ? h("button", { class: "m-btn primary", onclick: () => c.backToFloor() }, "Back to the floor")
        : h("button", { class: "m-btn primary", onclick: () => c.startDemo() }, r.started ? "Watch again" : "Start the demo")),
    card("report", "Initiating-coverage report", "Eleven sections, with charts, the thesis, risks and valuation.", `${c.report.sections.length - 1} sections · PDF and Word`),
    card("model", "Bull, base and bear model", "Checked against the valuation engine, with 2,000 simulations.", `Base ${usd(c.model.cases.base.price_target)} · ${c.model.rating}`),
    card("note", "Client note", "A short note that passed the publishing checks.", `${c.note.words} words · one X post · one LinkedIn post`),
    card("position", "Paper position", "What the team proposed, Audit checked and Stott approved.", "Paper money: nothing real moves"),
    note(c.banner),
  ];
}

// ---- the report ---------------------------------------------------------------------------------
export function renderMobileReport(c: ReaderCtx): HTMLElement[] {
  const r = c.report, last = r.sections.length - 1;
  const chips = h("div", { class: "mr-chips", role: "tablist", "aria-label": "Report sections" },
    ...r.sections.map((s, i) => h("button", { class: `mr-chip${i === c.section ? " active" : ""}`, role: "tab", "aria-selected": i === c.section ? "true" : "false",
      onclick: () => { tap(); c.setSection(i); } }, s.title)));
  requestAnimationFrame(() => {   // the row is rebuilt on each pick: bring the chosen chip back into view
    const on = chips.querySelector<HTMLElement>(".mr-chip.active");
    if (on) chips.scrollLeft = Math.max(0, on.offsetLeft - 16);
  });
  const body = h("div", { class: "demo-doc mr-doc" });
  body.innerHTML = r.sections[c.section].html.replace(/src="img\//g, `src="${c.base}img/`);
  return [
    h("div", { class: "mr-head" }, h("h2", {}, r.title), chip(r.rating, ratingTone(r.rating))),
    h("div", { class: "mr-actions" },
      h("a", { class: "m-btn quiet", href: `${c.base}${r.pdf}`, target: "_blank", rel: "noopener" }, "Open the PDF"),
      h("button", { class: "m-btn quiet", onclick: () => { tap(); share(`${c.base}${r.pdf}`, r.title); } }, "Share")),
    chips, body,
    h("div", { class: "mr-pager" },
      h("button", { class: "m-btn quiet", disabled: c.section === 0, onclick: () => { tap(); c.setSection(c.section - 1); } }, "‹ Previous"),
      h("button", { class: "m-btn primary", disabled: c.section === last, onclick: () => { tap(); c.setSection(c.section + 1); } }, "Next section ›")),
    note(c.banner),
  ];
}

// ---- the model ------------------------------------------------------------------------------------
export function renderMobileModel(c: ReaderCtx): HTMLElement[] {
  const m = c.model;
  const order = ["bear", "base", "bull"] as const;
  const lo = 0, hi = Math.max(m.cases.bull.price_target, m.price) * 1.1;
  const at = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  const [p10, p50, p90] = m.simulation.p10_p50_p90;
  const row = (k: string, v: string) => h("div", { class: "mr-kv" }, h("span", {}, k), h("strong", {}, v));
  const proj = m.cases.base.projections;
  return [
    h("div", { class: "mr-head" }, h("h2", {}, "Micron (MU): three cases"), chip(m.rating, ratingTone(m.rating))),
    h("p", { class: "mr-lede" }, `Price ${usd(m.price)} on ${m.as_of}. Over ${m.horizon_months} months the S&P 500 is expected to return ${pct(m.benchmark_return)}; the base case trails it by ${pct(Math.abs(m.excess_return))}, so the rating is ${m.rating}.`),
    h("div", { class: "demo-scale" },
      h("div", { class: "demo-track" },
        h("div", { class: "demo-range", style: `left:${at(p10)};width:${((p90 - p10) / (hi - lo)) * 100}%` }),
        ...order.map((k) => h("div", { class: `demo-dot ${k}`, style: `left:${at(m.cases[k].price_target)}` })),
        h("div", { class: "demo-now", style: `left:${at(m.price)}` })),
      h("div", { class: "demo-legend" }, h("span", {}, `Bear ${usd(m.cases.bear.price_target)}`), h("span", {}, `Base ${usd(m.cases.base.price_target)}`),
        h("span", {}, `Bull ${usd(m.cases.bull.price_target)}`), h("span", {}, `Now ${usd(m.price)}`))),
    ...order.map((k) => {
      const cs = m.cases[k];
      return h("div", { class: `m-card mr-case ${k}` },
        h("div", { class: "mr-case-top" }, h("span", { class: "m-card-label" }, `${k[0].toUpperCase()}${k.slice(1)} case`), h("span", { class: `mr-case-ret ${cs.total_return < 0 ? "down" : "up"}` }, `${pct(cs.total_return)} total return`)),
        h("div", { class: "mr-case-pt" }, usd(cs.price_target)),
        row("WACC", pct(cs.wacc)), row("DCF value", usd(cs.methods.dcf)), row("Forward P/E value", usd(cs.methods.pe)), row("EV/EBITDA value", usd(cs.methods.ev_ebitda)));
    }),
    h("h3", { class: "mr-h" }, "Base case path"),
    h("div", { class: "mr-scroll" },
      h("table", { class: "demo-table mr-table" },
        h("tr", {}, h("th", {}, ""), ...proj.map((p) => h("th", {}, `FY'${String(p.fy).slice(2)}E`))),
        h("tr", {}, h("th", {}, "Revenue ($ bn)"), ...proj.map((p) => h("td", {}, p.revenue.toFixed(1)))),
        h("tr", {}, h("th", {}, "EBITDA margin"), ...proj.map((p) => h("td", {}, pct(p.ebitda_margin)))),
        h("tr", {}, h("th", {}, "EPS"), ...proj.map((p) => h("td", {}, usd(p.eps)))),
        h("tr", {}, h("th", {}, "Free cash flow ($ bn)"), ...proj.map((p) => h("td", {}, p.fcf.toFixed(1)))))),
    h("h3", { class: "mr-h" }, `${m.simulation.runs?.toLocaleString()} simulations`),
    h("div", { class: "m-card" },
      row("Median", usd(p50)), row("80% of outcomes", `${usd(p10)} to ${usd(p90)}`),
      row("Target above today's price", pct(m.simulation.chance_above_price)), row("Beats the S&P 500", pct(m.simulation.chance_beats_sp500))),
    h("h3", { class: "mr-h" }, "What moves the target"),
    h("div", { class: "m-card" }, ...m.simulation.drivers.map((d) => row(d.driver, `${usd(d.pt_range[0])} to ${usd(d.pt_range[1])}`))),
    h("div", { class: "mr-actions" },
      h("a", { class: "m-btn quiet", href: `${c.base}${m.workbook}`, download: "MU_model_v1.xlsx" }, "Download the Excel model"),
      h("button", { class: "m-btn quiet", onclick: () => { tap(); share(`${c.base}${m.workbook}`, "Micron (MU) model v1"); } }, "Share")),
    note(c.banner),
  ];
}

// ---- the client note ------------------------------------------------------------------------------
export function renderMobileNote(c: ReaderCtx): HTMLElement[] {
  const n = c.note;
  const posts = n.social.split(/\n(?=#+\s)/).map((s) => s.trim()).filter(Boolean);
  const body = h("div", { class: "demo-doc mr-doc" });
  body.innerHTML = n.html;
  const copy = (text: string, btn: HTMLElement) => {
    const done = () => { btn.textContent = "Copied"; window.setTimeout(() => { btn.textContent = "Copy"; }, 1600); };
    if (navigator.clipboard?.writeText) void navigator.clipboard.writeText(text).then(done).catch(() => { /* blocked: leave the text selectable */ });
    else done();
    tap();
  };
  return [
    h("div", { class: "mr-head" }, h("h2", {}, n.title), chip("Passed the publishing checks", "good")),
    h("img", { class: "outbox-header", src: `${c.base}${n.header}`, alt: "" }),
    body,
    h("h3", { class: "mr-h" }, "Social posts"),
    ...posts.map((p) => {
      const btn = h("button", { class: "m-btn quiet mr-copy" }, "Copy");
      btn.addEventListener("click", () => copy(p, btn));
      return h("div", { class: "m-card mr-post" }, h("pre", { class: "demo-post" }, p), btn);
    }),
    note(c.banner),
  ];
}

// ---- the position -----------------------------------------------------------------------------------
export function renderMobilePosition(c: ReaderCtx): HTMLElement[] {
  const card = c.positionCard();
  return [card ?? h("p", { class: "empty" }, "The team has not opened a position yet."), note(c.banner)];
}
