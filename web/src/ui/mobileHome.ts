// The phone's summary home: what a visitor sees first when the office is quiet. Cards, not a
// map: whether anyone is working, how the paper portfolio is doing against the S&P 500, the
// latest published note, and the demo. Everything here is already public.
import type { OfficeState } from "../state";
import { h } from "./dom";
import { lastActiveLabel } from "./mobileModel";
import { pct, type PortfolioView } from "./portfolio";

export interface HomeDeps {
  state: OfficeState;
  portfolio: () => PortfolioView | null;
  latestNote: () => { title: string; date: string; words: number } | null;
  lastActive: () => number | null;           // seconds since epoch of the newest floor activity
  watch: () => void;                          // play the latest replay on the floor
  openFloor: () => void;
  openPortfolio: () => void;
  openNote: () => void;
  openDemo: () => void;
}

export class MobileHome {
  readonly root = h("div", { class: "m-home" });
  private shown = "";

  constructor(private d: HomeDeps) {}

  /** Redraw only when something it shows has changed (it is called on every state change). */
  render() {
    const working = [...this.d.state.agents.values()].filter((a) => a.status === "working");
    const pv = this.d.portfolio();
    const scored = !!pv && pv.return !== null && pv.priced;
    const note = this.d.latestNote();
    const last = this.d.lastActive();
    const key = JSON.stringify([working.length, this.d.state.touring, this.d.state.replaying, this.d.state.held, this.d.state.clockedOut, scored && [pv!.return, pv!.benchmark_return],
      pv && pv.value, note?.title, last && Math.floor(last / 60), Math.floor(Date.now() / 60_000)]);
    if (key === this.shown) return;
    this.shown = key;

    // A replay sets agent statuses too, but it is not live: never call it "working right now".
    const replaying = this.d.state.replaying;
    const touring = this.d.state.touring;   // the demo drives statuses too, and is not live either
    const busy = working.length > 0 && !replaying && !touring;
    const headline = touring ? "The Micron demo is running" : replaying ? "Replaying recent work" : this.d.state.held ? "The office is paused" : this.d.state.clockedOut ? "The office has clocked out" : busy ? `${working.length} working right now` : "The office is quiet";
    const sub = touring ? "A recorded run, not live. Pick it up where you left it." : replaying ? "The office is quiet; this is how a recent stretch of work went."
      : busy ? working.slice(0, 3).map((a) => a.nickname).join(", ") + (working.length > 3 ? ` and ${working.length - 3} more` : "") : lastActiveLabel(last);

    const cards: (HTMLElement | null)[] = [
      h("div", { class: "m-card m-hero", "data-busy": busy ? "1" : "0" },
        h("div", { class: "m-hero-top" }, h("span", { class: "m-dot" }), h("div", {}, h("div", { class: "m-hero-title" }, headline), h("div", { class: "m-hero-sub" }, sub))),
        h("button", { class: "m-btn primary", onclick: () => (busy || replaying || touring ? this.d.openFloor() : this.d.watch()) }, touring ? "Back to the demo" : replaying ? "Back to the replay" : busy ? "Watch live" : "Watch the latest replay"),
        h("button", { class: "m-btn quiet", onclick: () => this.d.openFloor() }, "Open the floor")),
      scored
        ? h("button", { class: "m-card m-score", onclick: () => this.d.openPortfolio() },
            h("div", { class: "m-card-label" }, "Paper portfolio vs the S&P 500"),
            h("div", { class: "m-score-row" },
              h("div", {}, h("div", { class: `m-score-num ${pv!.return! >= 0 ? "up" : "down"}` }, pct(pv!.return)), h("div", { class: "m-score-cap" }, "Portfolio")),
              h("div", {}, h("div", { class: "m-score-num" }, pct(pv!.benchmark_return)), h("div", { class: "m-score-cap" }, "S&P 500"))),
            h("div", { class: "m-card-foot" }, pv!.since ? `Since ${pv!.since}. No real money.` : "No real money."))
        : h("button", { class: "m-card m-score", onclick: () => this.d.openPortfolio() },
            h("div", { class: "m-card-label" }, "Paper portfolio vs the S&P 500"),
            h("div", { class: "m-card-body" }, "The scoreboard opens with the first position the Captain approves. No real money."),
            h("div", { class: "m-card-foot" }, "See the portfolio")),
      note
        ? h("button", { class: "m-card", onclick: () => this.d.openNote() },
            h("div", { class: "m-card-label" }, "Latest note"),
            h("div", { class: "m-card-title" }, note.title),
            h("div", { class: "m-card-foot" }, `${note.date} · ${note.words} words · Read it`))
        : null,
      h("button", { class: "m-card", onclick: () => this.d.openDemo() },
        h("div", { class: "m-card-label" }, "See how it works"),
        h("div", { class: "m-card-title" }, "Watch the office build a Micron report"),
        h("div", { class: "m-card-foot" }, "A five-minute demo · no AI calls")),
    ];
    this.root.replaceChildren(...cards.filter((c): c is HTMLElement => c !== null));
  }
}
