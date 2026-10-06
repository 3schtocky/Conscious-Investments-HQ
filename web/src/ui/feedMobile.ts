// The phone's Feed: a work log. Assignments are threads (who owns it, what happened, whether it is
// done) under day headings, with a wing colour as the rail so a glance says which team is moving.
// Built from the same public events as the desktop feed, via feedModel.ts.
import { WING_ACCENT } from "../office/map";
import type { OfficeState } from "../state";
import { h, plain } from "./dom";
import { buildFeed, filterFeed, groupByDay, itemTs, shortAgo, type FeedItem, type Note, type Thread } from "./feedModel";
import { tap } from "./haptics";
import { WING_ORDER } from "./panels";

export class MobileFeed {
  /** The wing the visitor is looking at, or null for everything. */
  wing: string | null = null;
  private chipsScroll = 0;

  constructor(private root: HTMLElement, private state: OfficeState, private onPick: (id: string) => void) {}

  render() {
    const prevTop = this.root.scrollTop, prevHeight = this.root.scrollHeight;
    this.chipsScroll = this.root.querySelector<HTMLElement>(".mf-chips")?.scrollLeft ?? this.chipsScroll;
    const all = buildFeed(this.state.feed, { name: (id) => this.state.name(id), wingOf: (id) => this.state.agents.get(id)?.wing });
    const items = filterFeed(all, this.wing);
    const now = Date.now();
    const body: HTMLElement[] = [];
    if (!items.length) {
      body.push(h("p", { class: "mf-empty" }, this.wing
        ? `Nothing from ${this.state.wings[this.wing] ?? "this wing"} yet. Work shows up here the moment they pick it up.`
        : "Quiet right now. Work shows up here the moment the office picks it up."));
    }
    for (const g of groupByDay(items, now)) {
      body.push(h("h2", { class: "mf-day" }, g.label));
      for (const it of g.items) body.push(it.kind === "thread" ? this.thread(it, now) : this.note(it, now));
    }
    this.root.classList.add("mf");
    this.root.replaceChildren(this.chips(), ...body);
    const chips = this.root.querySelector<HTMLElement>(".mf-chips");
    if (chips) chips.scrollLeft = this.chipsScroll;
    // keep the reader's place: if they have scrolled, hold the view steady as new work lands on top
    this.root.scrollTop = prevTop > 4 ? prevTop + (this.root.scrollHeight - prevHeight) : 0;
  }

  private chips(): HTMLElement {
    const wings = WING_ORDER.filter((w) => w !== "executive");
    const chip = (label: string, wing: string | null) =>
      h("button", { class: `mf-chip${this.wing === wing ? " active" : ""}`, style: wing ? `--accent:${WING_ACCENT[wing] ?? "#46505e"}` : "",
        "aria-pressed": this.wing === wing ? "true" : "false", onclick: () => { tap(); this.wing = wing; this.render(); } },
      wing ? h("i", { "aria-hidden": "true" }) : null, label);
    return h("div", { class: "mf-chips", role: "toolbar", "aria-label": "Filter by wing" },
      chip("All", null), ...wings.map((w) => chip(this.state.wings[w] ?? w, w)));
  }

  private thread(t: Thread, now: number): HTMLElement {
    const a = this.state.agents.get(t.agent);
    const live = t.endTs === null;
    return h("article", { class: `mf-thread ${live ? "open" : "closed"}`, style: `--accent:${WING_ACCENT[t.wing] ?? "#46505e"}` },
      h("div", { class: "mf-head" }, h("h3", { class: "mf-title" }, plain(t.title)), h("time", { class: "mf-time" }, shortAgo(itemTs(t), now))),
      h("div", { class: "mf-meta" },
        h("button", { class: "mf-who", onclick: () => this.onPick(t.agent) }, this.state.name(t.agent)),
        h("span", { class: "mf-wing" }, this.state.wings[t.wing] ?? "")),
      t.steps.length ? h("ol", { class: "mf-steps" }, ...t.steps.map((s) => h("li", { class: s.kind }, s.text))) : null,
      live ? h("div", { class: "mf-state" }, h("i", { "aria-hidden": "true" }), a?.status === "working" ? "Working on it" : "In progress") : null);
  }

  private note(n: Note, now: number): HTMLElement {
    return h("div", { class: "mf-note", style: `--accent:${(n.wing && WING_ACCENT[n.wing]) || "#8c8880"}` },
      h("i", { "aria-hidden": "true" }), h("span", { class: "mf-note-text" }, plain(n.text)), h("time", { class: "mf-time" }, shortAgo(n.ts, now)));
  }
}

export type { FeedItem };
