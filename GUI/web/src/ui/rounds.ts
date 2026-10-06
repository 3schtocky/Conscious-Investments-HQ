// Rounds: where every wing stands right now (read from the live activity log, free) and what each
// delegate told Juno on her last walk. Works live and while the office is paused.
import type { OfficeState } from "../state";
import { clear, h, timeAgo } from "./dom";

interface Person {
  id: string; name: string; role: string; status: string; working_on: string | null;
  turns: number; minutes_on_task: number | null; recent: string[];
}
interface Wing {
  wing: string; name: string; state: string; people: Person[]; blockers: string[];
  waiting_on_captain: string[]; queued: number; headline: string;
}
interface Round {
  ts: number; mode: "model" | "code"; reason: string; held: boolean; summary: string;
  wings: { wing: string; delegate: string; answer: string }[];
}
interface RoundsView { digest: { held: boolean; clocked_out: boolean; active: boolean; wings: Wing[] }; last_round: Round | null }

const STATE: Record<string, string> = {
  working: "Working", blocked: "Blocked", waiting: "Waiting on you", held: "Paused", queued: "Queued", idle: "Idle",
};

export class RoundsPanel {
  view: RoundsView | null = null;
  private loadedAt = 0;
  private inFlight = false;
  private again = false;
  private walking = false;

  constructor(private root: HTMLElement, private state: OfficeState, private onChange: () => void) {}

  /** Wings needing the Captain: blocked, or waiting on a decision. */
  get count(): number {
    return this.view?.digest.wings.filter((w) => w.state === "blocked" || w.state === "waiting").length ?? 0;
  }

  async refresh() {
    if (this.inFlight) { this.again = true; return; }
    this.inFlight = true;
    this.loadedAt = Date.now();
    try {
      const res = await fetch("/api/rounds");
      if (res.ok) { this.view = await res.json(); this.onChange(); }
    } finally {
      this.inFlight = false;
      if (this.again) { this.again = false; this.refresh(); }
    }
  }

  /** The board is cheap code, so keep it close to live while the tab is open. */
  maybeRefresh() { if (Date.now() - this.loadedAt > 8_000) this.refresh(); }

  private async walk() {
    this.walking = true;
    this.render();
    try { await fetch("/api/rounds/walk", { method: "POST" }); } finally { this.walking = false; }
    await this.refresh();
  }

  render() {
    const top = this.root.scrollTop;
    clear(this.root);
    const v = this.view;
    if (!v) { this.root.append(h("p", { class: "empty" }, "Loading the rounds…")); return; }
    const d = v.digest, last = v.last_round;
    const answers = new Map((last?.wings ?? []).map((w) => [w.wing, w]));
    const juno = this.state.name("chief_of_staff");
    const status = d.held ? "The office is paused. Work waits where it is; this board reads each wing's live log."
      : d.clocked_out ? "The office has clocked out for the day."
      : d.active ? "Live: each wing's delegate is reading its lead's activity as it happens." : "Every wing is idle.";
    this.root.append(
      h("div", { class: "rounds-head" },
        h("div", {}, h("strong", {}, "Rounds"), h("p", { class: "muted" }, status)),
        h("button", { class: "primary", disabled: this.walking, onclick: () => this.walk() }, this.walking ? `${juno} is walking…` : `Ask ${juno} to walk the floor`)),
    );
    if (last) this.root.append(h("p", { class: "muted rounds-last" }, `${juno}'s last walk ${timeAgo(last.ts)} · ${last.mode === "model" ? "the delegates answered" : "read straight from the log (no model turns)"}`));
    const busy = d.wings.filter((w) => w.state !== "idle"), idle = d.wings.filter((w) => w.state === "idle");
    for (const w of busy) {
      const told = answers.get(w.wing);
      this.root.append(h("section", { class: `round-wing state-${w.state}` },
        h("header", {}, h("strong", {}, w.name), h("span", { class: `round-pill state-${w.state}` }, STATE[w.state] ?? w.state)),
        h("p", { class: "round-say" }, told ? `${told.delegate}: ${told.answer}` : w.headline),
        ...w.people.filter((p) => p.working_on || p.status !== "idle").map((p) => h("div", { class: "round-person" },
          h("div", {}, h("strong", {}, p.name), ` · ${p.role} · ${p.status}`,
            p.working_on ? ` · ${p.working_on} (${p.turns} turns, ${p.minutes_on_task ?? 0} min)` : ""),
          p.recent.length ? h("ul", {}, ...p.recent.slice(-3).map((r) => h("li", {}, r))) : null)),
        w.blockers.length ? h("p", { class: "round-warn" }, `Blocked: ${w.blockers.join("; ")}`) : null,
        w.waiting_on_captain.length ? h("p", { class: "round-warn" }, `Waiting on you: ${w.waiting_on_captain.join("; ")}`) : null));
    }
    if (!busy.length) this.root.append(h("p", { class: "empty" }, "Nothing is in motion. Assign work to get the office moving."));
    if (idle.length) this.root.append(h("p", { class: "muted" }, `Idle: ${idle.map((w) => w.name).join(", ")}`));
    this.root.scrollTop = top;
  }
}
