// The five-minute Micron demo. A visitor presses Demo and their own browser plays a recorded run of the
// real office (see hq/demo_micron.py and hq/demo_script.py) from 0:00: the floor moves, the scripted
// lines appear in bubbles and the Feed, and the report, the model and the client note unlock as the
// office makes them. Nothing here talks to the server after the files load, so every visitor has their
// own timeline and pressing Demo again simply starts over.
import "./demoTour.css";
import type { OfficeScene } from "../office/scene";
import type { OfficeEvent, OfficeState } from "../state";
import { clear, h } from "./dom";
import { pct, ratingTone, usd } from "./demoFormat";
import { renderMobileFiles, renderMobileModel, renderMobileNote, renderMobilePosition, renderMobileReport, type FileView, type ReaderCtx } from "./demoReaders";

export const BASE = `${import.meta.env.BASE_URL}demo/micron/`;
const TICK_MS = 100;

interface Beat extends Record<string, unknown> { t: number; type: string; agent: string | null }
interface Act { t: number; label: string; caption: string }
interface Unlock { t: number; artifact: "report" | "model" | "note" | "position"; label: string }
interface Tour { duration: number; end_card: number; beats: Beat[]; acts: Act[]; unlocks: Unlock[] }
export interface ReportData { title: string; date: string; rating: string; banner: string; pdf: string; sections: { id: string; title: string; html: string }[] }
export interface Case { price_target: number; total_return: number; wacc: number; methods: Record<string, number>; projections: { fy: number; revenue: number; growth: number; ebitda_margin: number; eps: number; capex: number; fcf: number }[] }
export interface ModelData {
  banner: string; price: number; as_of: string; rating: string; benchmark_return: number; excess_return: number; horizon_months: number; target_date: string;
  cases: Record<"bear" | "base" | "bull", Case>; workbook: string;
  simulation: { runs: number; p10_p50_p90: number[]; chance_above_price: number; chance_beats_sp500: number; drivers: { driver: string; pt_range: number[] }[] };
}
export interface PositionData {
  ticker: string; name: string; size_pct: number; entry_price: number; shares: number; value: number; portfolio_value: number;
  entered: string; rating: string; base_target: number; to_target: number; bear_target: number; bull_target: number;
  model_version: number; thesis: string; proposed_by: string; return: number; vs_sp500: number; downloads: { label: string; href: string }[];
}
export interface NoteData { banner: string; title: string; words: number; html: string; social: string; header: string }

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const get = async <T>(file: string): Promise<T> => {
  const res = await fetch(`${BASE}${file}`);
  if (!res.ok) throw new Error(`${file}: ${res.status}`);
  return res.json();
};

/** Drives the recorded run and owns the bar over the floor. */
export class DemoTour {
  readonly bar = h("div", { class: "tour-bar hidden", role: "status", "aria-live": "polite" });
  readonly panel: DemoPanel;
  private tour: Tour | null = null;
  private timer: number | undefined;
  private t0 = 0;            // performance.now() when playback (re)started, minus time spent paused
  private pausedAt: number | null = null;
  private idx = 0;
  private seq = 0;
  private unlocked = new Set<string>();
  private finished = false;
  private listeners = new Set<() => void>();
  /** Called when the office finishes a file while the demo plays (not when skipping past it). */
  onUnlock: ((artifact: string, label: string) => void) | null = null;

  constructor(private state: OfficeState, private scene: () => OfficeScene | null,
              private restore: () => Promise<void>, private onChange: () => void,
              private openTab: () => void, private openPortfolio: () => void) {
    this.panel = new DemoPanel(() => this.unlocked, onChange, (id) => this.state.agents.has(id) ? this.state.name(id) : id,
      () => this.state.captain.nickname);
  }

  get active(): boolean { return this.state.touring; }

  /** The phone player draws itself from this, and redraws whenever it changes. */
  subscribe(fn: () => void): () => void { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  private emit() { for (const fn of this.listeners) fn(); }

  view(): { active: boolean; finished: boolean; paused: boolean; now: number; duration: number; acts: Act[]; unlocked: Set<string>; banner: string } | null {
    if (!this.tour) return null;
    return { active: this.state.touring, finished: this.finished, paused: this.pausedAt !== null, now: this.playhead(), duration: this.tour.duration,
      acts: this.tour.acts, unlocked: new Set(this.unlocked), banner: this.panel.banner };
  }

  /** Stop the clock if it is running (the visitor has gone to read a file). They resume when they are back. */
  pauseIfPlaying() {
    if (this.state.touring && !this.finished && this.pausedAt === null) this.togglePause();
  }

  /** Jump to a moment, rebuilding the floor, feed and files as they were then. Walks and speech
   *  between 0:00 and there are skipped: everyone just ends up where they would be. */
  seek(t: number) {
    if (!this.tour) return;
    const target = Math.max(0, Math.min(t, this.tour.end_card - 0.5));
    const wasPaused = this.pausedAt !== null;
    window.clearInterval(this.timer);
    this.unlocked.clear();
    this.panel.reset();
    this.finished = false;
    this.idx = 0;
    this.state.feed = [];
    const scene = this.scene();
    if (scene) scene.instant = true;
    try {
      this.floorToIdle();
      while (this.idx < this.tour.beats.length && this.tour.beats[this.idx].t < target) this.play(this.tour.beats[this.idx++]);
    } finally {
      if (scene) scene.instant = false;
    }
    for (const u of this.tour.unlocks) if (u.t < target) this.unlocked.add(u.artifact);
    this.t0 = performance.now() - target * 1000;
    this.pausedAt = wasPaused ? performance.now() : null;
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    this.draw();
    this.onChange();
  }

  private deliverables(): number { return ["report", "model", "note"].filter((k) => this.unlocked.has(k)).length; }

  /** Press Demo: start from the beginning, whatever the state of any run before it. */
  async start() {
    window.clearInterval(this.timer);
    try {
      this.tour ??= await get<Tour>("tour.json");
      await this.panel.load();
    } catch {
      this.bar.classList.remove("hidden");
      clear(this.bar);
      this.bar.append(h("span", {}, "The demo could not be loaded. Please try again in a moment."),
        h("button", { class: "tour-btn", onclick: () => this.stop() }, "Close"));
      return;
    }
    this.state.touring = true;
    this.state.replaying = false;
    this.unlocked.clear();
    this.finished = false;
    this.idx = 0;
    this.pausedAt = null;
    this.state.feed = [];
    this.floorToIdle();
    this.panel.reset();
    this.t0 = performance.now();
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    document.body.classList.add("touring");
    this.draw();
    this.onChange();
  }

  stop() {
    window.clearInterval(this.timer);
    this.state.touring = false;
    this.bar.classList.add("hidden");
    document.body.classList.remove("touring");
    this.emit();
    void this.restore().then(() => this.onChange());
  }

  togglePause() {
    if (this.finished) return;
    if (this.pausedAt === null) this.pausedAt = performance.now();
    else { this.t0 += performance.now() - this.pausedAt; this.pausedAt = null; }
    this.draw();
  }

  private playhead(): number {
    const now = this.pausedAt ?? performance.now();
    return Math.min((now - this.t0) / 1000, this.tour!.duration);
  }

  /** Everyone at their own desk, nobody working, as at 0:00. */
  private floorToIdle() {
    const ev = (e: Record<string, unknown>) => ({ id: -(++this.seq), ts: Date.now() / 1000, task_id: null, ...e }) as unknown as OfficeEvent;
    for (const id of this.state.agents.keys()) {
      this.state.apply(ev({ type: "status", agent: id, status: "idle" }));
      this.scene()?.handle(ev({ type: "move", agent: id, to: `desk:${id}` }));
    }
    this.scene()?.handle(ev({ type: "move", agent: "captain", to: "desk:captain" }));
  }

  private tick() {
    if (!this.tour || this.pausedAt !== null) return;
    const now = this.playhead();
    while (this.idx < this.tour.beats.length && this.tour.beats[this.idx].t <= now) this.play(this.tour.beats[this.idx++]);
    for (const u of this.tour.unlocks) {
      if (u.t <= now && !this.unlocked.has(u.artifact)) { this.unlocked.add(u.artifact); this.panel.noticed(u.artifact); this.onChange(); this.onUnlock?.(u.artifact, u.label); }
    }
    if (now >= this.tour.duration && !this.finished) { this.finished = true; window.clearInterval(this.timer); }
    this.draw();
  }

  /** Fill {role_id} placeholders with the nickname the visitor sees, then hand the event to the floor. */
  private play(beat: Beat) {
    const fill = (v: unknown): unknown => {
      if (typeof v === "string") return v.replace(/\{(\w+)\}/g, (m, id) => (this.state.agents.has(id) ? this.state.name(id) : m));
      if (Array.isArray(v)) return v.map(fill);
      return v;
    };
    const ev: Record<string, unknown> = { id: -(++this.seq), ts: Date.now() / 1000, task_id: null };
    for (const [k, v] of Object.entries(beat)) if (k !== "t") ev[k] = fill(v);
    const event = ev as unknown as OfficeEvent;
    this.state.apply(event);
    this.scene()?.handle(event);
    this.onChange();
  }

  private draw() {
    if (!this.tour) return;
    this.emit();
    const now = this.playhead();
    const act = [...this.tour.acts].reverse().find((a) => a.t <= now) ?? this.tour.acts[0];
    const step = this.tour.acts.indexOf(act) + 1;
    clear(this.bar);
    this.bar.classList.remove("hidden");
    if (this.finished) {
      this.bar.append(h("div", { class: "tour-end" },
        h("strong", {}, "That was the whole office, start to finish, in five minutes."),
        h("span", {}, "A recorded run of the real tools on real Micron filings. The report, the model and the client note are in the Demo tab."),
        h("div", { class: "tour-actions" },
          h("button", { class: "tour-btn primary", onclick: () => void this.start() }, "Watch again"),
          h("button", { class: "tour-btn", onclick: () => this.openTab() }, "Open the deliverables"),
          h("button", { class: "tour-btn", onclick: () => this.stop() }, "Close the demo"))),
        h("div", { class: "tour-note" }, this.panel.banner));
      return;
    }
    this.bar.append(
      h("div", { class: "tour-row" },
        h("span", { class: "tour-step" }, `${step} of ${this.tour.acts.length}`),
        h("strong", {}, act.label),
        h("span", { class: "tour-caption" }, act.caption),
        h("span", { class: "tour-clock" }, `${clock(now)} / ${clock(this.tour.duration)}`),
        h("div", { class: "tour-actions" },
          h("button", { class: "tour-btn", onclick: () => this.togglePause() }, this.pausedAt === null ? "Pause" : "Resume"),
          h("button", { class: "tour-btn", onclick: () => void this.start() }, "Restart"),
          this.deliverables() ? h("button", { class: "tour-btn primary", onclick: () => this.openTab() }, `Deliverables ${this.deliverables()}/3`) : null,
          this.unlocked.has("position") ? h("button", { class: "tour-btn primary", onclick: () => this.openPortfolio() }, "See the position") : null,
          h("button", { class: "tour-btn", onclick: () => this.stop() }, "Close"))),
      h("div", { class: "tour-progress", "aria-hidden": "true" },
        h("div", { class: "tour-fill", style: `width:${(now / this.tour.duration) * 100}%` }),
        ...this.tour.acts.slice(1).map((a) => h("i", { class: "tour-tick", style: `left:${(a.t / this.tour!.duration) * 100}%` }))),
      h("div", { class: "tour-note" }, this.panel.banner));
  }
}

/** The Demo tab: the report, the model and the client note, each locked until the office makes it. */
export class DemoPanel {
  readonly root = h("div", { class: "demo-panel" });
  banner = "Demo: Micron (MU) illustrative research, not investment advice. Real SEC data and a real model engine; the agents are scripted and no AI model was called.";
  private report: ReportData | null = null;
  private model: ModelData | null = null;
  private note: NoteData | null = null;
  private position: PositionData | null = null;
  private view: FileView = "home";
  /** Set by the phone layout: how the files list talks to the player. */
  mobileOps: { startDemo: () => void; backToFloor: () => void; status: () => { playing: boolean; finished: boolean; started: boolean } } | null = null;
  private lastKey = "";
  private section = 0;
  private fresh = new Set<string>();
  private sig = "";   // what was last drawn: the tour asks for a redraw on every beat, and a reader's scroll must survive

  constructor(private unlocked: () => Set<string>, private onChange: () => void,
              private name: (id: string) => string, private captain: () => string) {}

  async load() {
    if (this.report) return;
    [this.report, this.model, this.note, this.position] = await Promise.all([get<ReportData>("report.json"), get<ModelData>("model.json"),
      get<NoteData>("note.json"), get<PositionData>("position.json")]);
    this.banner = this.report.banner;
  }


  /** The Portfolio tab's card for the demo position, once the team has opened it. Same look as a Gems card. */
  positionCard(): HTMLElement | null {
    const p = this.position;
    if (!p || !this.unlocked().has("position")) return null;
    const fill = (s: string) => s.replace(/\{(\w+)\}/g, (_m, id) => (id === "captain" ? this.captain() : this.name(id)));
    const cell = (label: string, value: string, cls = "") => h("div", {}, h("span", { class: "muted small" }, label), h("strong", { class: cls }, value));
    return h("div", { class: "watch s-open demo-position" },
      h("div", { class: "watch-head" },
        h("span", { class: "watch-ticker" }, p.ticker),
        h("span", { class: "chip good" }, "Open position"),
        h("span", { class: "chip ticker" }, `${p.size_pct}% of portfolio`),
        h("span", { class: "chip good" }, p.rating),
        h("span", { class: "feed-time" }, "Just opened")),
      h("p", { class: "watch-thesis" }, `${p.name}: the team's call after the full initiation. ${this.name("er_lead")} proposed it, ${this.name("audit_lead")} checked the rules and ${this.captain()} approved it at ${p.size_pct}% of the paper portfolio.`),
      h("div", { class: "scorecard" },
        cell("Entry price", usd(p.entry_price)), cell("Position", usd(p.value)),
        cell("Since entry", pct(p.return), "up"), cell("vs S&P 500", pct(p.vs_sp500), "up")),
      h("div", { class: "scorecard" },
        cell("Base target", usd(p.base_target), "up"), cell("To base", pct(p.to_target), "up"),
        cell("Bull case", usd(p.bull_target)), cell("Bear case", usd(p.bear_target), "down")),
      h("div", { class: "muted small" }, `${p.shares.toFixed(2)} shares of ${usd(p.portfolio_value)} · entered ${p.entered} · Quant model v${p.model_version}. Paper money: nothing real moves.`),
      h("details", { class: "digest-past" }, h("summary", {}, "Why the team bought it"), h("p", { class: "watch-thesis" }, fill(p.thesis))),
      h("div", { class: "row demo-downloads" },
        ...p.downloads.map((d, i) => h("a", { class: i < 2 ? "btn small-btn" : "btn ghost small-btn", href: `${BASE}${d.href}`, download: d.href.split("/").pop() ?? "", target: "_blank", rel: "noopener" }, d.label))),
      h("div", { class: "muted small" }, "Demo position on Micron (MU). Illustrative research, not investment advice."));
  }

  reset() { this.view = "home"; this.section = 0; this.fresh.clear(); this.sig = ""; this.render(); }
  noticed(artifact: string) { this.fresh.add(artifact); }
  get count(): number { return [...this.fresh].filter((k) => k !== "position").length; }

  private open(view: "report" | "model" | "note") { this.view = view; this.fresh.delete(view); this.render(); this.onChange(); }

  // ---- the phone layout drives these ----
  /** Open one of the files (the toast's Open button, a row in the files list). */
  show(view: FileView) { this.view = view; this.fresh.delete(view); this.sig = ""; this.render(); this.onChange(); }
  /** One step back inside the files: true if it did something, false if it is already on the list. */
  back(): boolean {
    if (this.view === "home") return false;
    this.view = "home"; this.sig = ""; this.render(); this.onChange();
    return true;
  }
  /** The masthead title for the file on screen. */
  get pageTitle(): string {
    return { home: "Demo files", report: "Report", model: "Model", note: "Client note", position: "Paper position" }[this.view];
  }
  get onList(): boolean { return this.view === "home"; }

  private renderMobile(have: Set<string>) {
    clear(this.root);
    if (!this.report || !this.model || !this.note) {
      const st = this.mobileOps?.status();
      this.root.append(h("div", { class: "m-card mr-intro" },
        h("div", { class: "m-card-label" }, "Micron (MU) demo"),
        h("div", { class: "m-card-body" }, "A five-minute recorded run: the office makes a report, a model and a client note, then sizes a paper position. The files appear here as it goes."),
        h("button", { class: "m-btn primary", onclick: () => this.mobileOps?.startDemo() }, st?.started ? "Watch again" : "Start the demo")));
      return;
    }
    const st = this.mobileOps?.status() ?? { playing: false, finished: false, started: false };
    const ctx: ReaderCtx = {
      base: BASE, report: this.report, model: this.model, note: this.note,
      section: this.section, setSection: (i) => { this.section = i; this.render(); },
      have, fresh: this.fresh, open: (v) => this.show(v),
      positionCard: () => this.positionCard(),
      run: { ...st, clock: "" },
      startDemo: () => this.mobileOps?.startDemo(), backToFloor: () => this.mobileOps?.backToFloor(),
      banner: this.banner,
    };
    const pages = { home: renderMobileFiles, report: renderMobileReport, model: renderMobileModel, note: renderMobileNote, position: renderMobilePosition };
    this.root.append(...pages[this.view](ctx));
    const key = `${this.view}:${this.section}`;
    if (key !== this.lastKey) { this.lastKey = key; this.root.scrollTop = 0; }   // a new page starts at the top
  }

  render() {
    const have = this.unlocked();
    const mobile = document.body.classList.contains("mobile");
    const run = mobile ? this.mobileOps?.status() : null;
    const sig = JSON.stringify([this.view, this.section, [...have].filter((k) => mobile || k !== "position"), [...this.fresh], !!this.report, mobile, run && [run.playing, run.finished, run.started]]);
    if (sig === this.sig) return;
    this.sig = sig;
    if (mobile) { this.renderMobile(have); return; }
    if (this.view === "position") this.view = "home";   // the position page exists only on a phone
    clear(this.root);
    this.root.append(h("div", { class: "demo-banner" }, this.banner));
    if (!this.report || !this.model || !this.note) {
      this.root.append(h("p", { class: "empty" }, "Press Demo at the top to watch the office work on Micron. The report, the model and the client note appear here as the office makes them."));
      return;
    }
    if (this.view === "home") { this.home(have); return; }
    this.root.append(h("button", { class: "demo-back", onclick: () => { this.view = "home"; this.render(); } }, "‹ All deliverables"));
    if (this.view === "report") this.reportView();
    if (this.view === "model") this.modelView();
    if (this.view === "note") this.noteView();
  }

  private home(have: Set<string>) {
    const card = (key: "report" | "model" | "note", title: string, blurb: string, meta: string) =>
      h("button", { class: `demo-card${have.has(key) ? "" : " locked"}`, disabled: !have.has(key), onclick: () => this.open(key) },
        h("div", { class: "demo-card-top" }, h("strong", {}, title),
          have.has(key) ? (this.fresh.has(key) ? h("span", { class: "chip good" }, "New") : h("span", { class: "chip" }, "Ready")) : h("span", { class: "chip" }, "Not made yet")),
        h("div", { class: "muted small" }, have.has(key) ? blurb : "The office has not produced this yet. Keep watching."),
        have.has(key) ? h("div", { class: "muted small" }, meta) : null);
    this.root.append(
      h("p", { class: "muted small audit-intro" }, "What the office made. Each one is the real file the agents worked on."),
      card("report", "Initiating-coverage report", "Eleven sections with charts, a thesis, risks and the valuation.", `${this.report!.sections.length - 1} sections · 16 pages as a PDF`),
      card("model", "Bull, base and bear model", "A formula workbook, checked against the valuation engine, with 2,000 simulations.", `Base ${usd(this.model!.cases.base.price_target)} · ${this.model!.rating}`),
      card("note", "Client note", "A short note that passed the publishing checks.", `${this.note!.words} words · one X post · one LinkedIn post`));
  }

  private reportView() {
    const r = this.report!;
    this.root.append(
      h("div", { class: "demo-head" }, h("strong", {}, r.title), h("span", { class: `chip ${ratingTone(r.rating)}` }, r.rating)),
      h("div", { class: "row" }, h("a", { class: "btn small-btn", href: `${BASE}${r.pdf}`, target: "_blank", rel: "noopener" }, "Open the PDF")),
      h("div", { class: "demo-sections" }, ...r.sections.map((s, i) =>
        h("button", { class: `demo-sec${i === this.section ? " active" : ""}`, onclick: () => { this.section = i; this.render(); } }, s.title))));
    const body = h("div", { class: "demo-doc" });
    body.innerHTML = r.sections[this.section].html.replace(/src="img\//g, `src="${BASE}img/`);
    this.root.append(body);
  }

  private modelView() {
    const m = this.model!;
    const order = ["bear", "base", "bull"] as const;
    const lo = 0, hi = Math.max(m.cases.bull.price_target, m.price) * 1.1;
    const at = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
    const [p10, p50, p90] = m.simulation.p10_p50_p90;
    this.root.append(
      h("div", { class: "demo-head" }, h("strong", {}, "Micron (MU): three cases"), h("span", { class: `chip ${ratingTone(m.rating)}` }, m.rating)),
      h("p", { class: "muted small" }, `Price ${usd(m.price)} on ${m.as_of}. Over ${m.horizon_months} months the S&P 500 is expected to return ${pct(m.benchmark_return)}; the base case trails it by ${pct(Math.abs(m.excess_return))}, so the rating is ${m.rating}.`),
      h("div", { class: "demo-scale" },
        h("div", { class: "demo-track" },
          h("div", { class: "demo-range", style: `left:${at(p10)};width:${((p90 - p10) / (hi - lo)) * 100}%`, title: "Simulation P10 to P90" }),
          ...order.map((k) => h("div", { class: `demo-dot ${k}`, style: `left:${at(m.cases[k].price_target)}`, title: `${k} ${usd(m.cases[k].price_target)}` })),
          h("div", { class: "demo-now", style: `left:${at(m.price)}`, title: "Current price" })),
        h("div", { class: "demo-legend" }, h("span", {}, `Bear ${usd(m.cases.bear.price_target)}`), h("span", {}, `Base ${usd(m.cases.base.price_target)}`),
          h("span", {}, `Bull ${usd(m.cases.bull.price_target)}`), h("span", {}, `Now ${usd(m.price)}`))),
      this.table(["", "Bear", "Base", "Bull"], [
        ["Price target", ...order.map((k) => usd(m.cases[k].price_target))],
        ["Total return", ...order.map((k) => pct(m.cases[k].total_return))],
        ["WACC", ...order.map((k) => pct(m.cases[k].wacc))],
        ["DCF value", ...order.map((k) => usd(m.cases[k].methods.dcf))],
        ["Forward P/E value", ...order.map((k) => usd(m.cases[k].methods.pe))],
        ["EV/EBITDA value", ...order.map((k) => usd(m.cases[k].methods.ev_ebitda))]]),
      h("h4", {}, "Base case path"),
      this.table(["", ...m.cases.base.projections.map((p) => `FY'${String(p.fy).slice(2)}E`)], [
        ["Revenue ($ bn)", ...m.cases.base.projections.map((p) => p.revenue.toFixed(1))],
        ["EBITDA margin", ...m.cases.base.projections.map((p) => pct(p.ebitda_margin))],
        ["EPS", ...m.cases.base.projections.map((p) => usd(p.eps))],
        ["Free cash flow ($ bn)", ...m.cases.base.projections.map((p) => p.fcf.toFixed(1))]]),
      h("h4", {}, `${m.simulation.runs?.toLocaleString()} simulations`),
      h("p", { class: "muted small" }, `Median ${usd(p50)}, with 80% of outcomes between ${usd(p10)} and ${usd(p90)}. The chance the price target is above today's price is ${pct(m.simulation.chance_above_price)}, and the chance it beats the S&P 500 is ${pct(m.simulation.chance_beats_sp500)}.`),
      this.table(["What moves the target", "Range"], m.simulation.drivers.map((d) => [d.driver, `${usd(d.pt_range[0])} to ${usd(d.pt_range[1])}`])),
      h("div", { class: "row" }, h("a", { class: "btn small-btn", href: `${BASE}${m.workbook}`, download: "MU_model_v1.xlsx" }, "Download the Excel model")));
  }

  private noteView() {
    const n = this.note!;
    const posts = n.social.split(/\n(?=#+\s)/).map((s) => s.trim()).filter(Boolean);
    const body = h("div", { class: "demo-doc" });
    body.innerHTML = n.html;
    this.root.append(
      h("div", { class: "demo-head" }, h("strong", {}, n.title), h("span", { class: "chip good" }, "Passed the publishing checks")),
      h("img", { class: "outbox-header", src: `${BASE}${n.header}`, alt: "" }),
      body,
      h("h4", {}, "Social posts"),
      ...posts.map((p) => h("pre", { class: "demo-post" }, p)));
  }

  private table(head: string[], rows: string[][]): HTMLElement {
    return h("table", { class: "demo-table" }, h("tr", {}, ...head.map((c) => h("th", {}, c))),
      ...rows.map((r) => h("tr", {}, ...r.map((c, i) => h(i === 0 ? "th" : "td", {}, c)))));
  }
}
