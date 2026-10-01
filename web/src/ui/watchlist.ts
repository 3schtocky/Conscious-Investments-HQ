// Watchlist: names Screening shortlisted, with a scorecard since they were flagged.
import type { OfficeState } from "../state";
import { clear, h, timeAgo } from "./dom";

interface Watch {
  id: number; ticker: string; added: number; added_by: string; added_by_name: string; source: string;
  thesis: string; pitch: string | null; price_at_add: number | null; status: string;
  price_now: number | null; return: number | null; spy_return: number | null; vs_spy: number | null;
}

const pct = (x: number | null) => (x === null || x === undefined ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`);
const px = (x: number | null) => (x === null || x === undefined ? "n/a" : `$${x.toFixed(2)}`);

export class WatchlistPanel {
  items: Watch[] = [];
  private pitches = new Map<string, string>();
  private open = new Set<number>();
  private busy = new Set<number>();
  private loadedAt = 0;

  constructor(private root: HTMLElement, private state: OfficeState, private onChange: () => void) {}

  get count(): number { return this.items.filter((w) => w.status === "watching").length; }

  private inFlight = false;

  async refresh() {
    if (this.inFlight) return;
    this.inFlight = true;
    this.loadedAt = Date.now();   // throttle even if this fetch fails
    try {
      const res = await fetch("/api/watchlist");
      if (!res.ok) return;
      this.items = await res.json();
      this.onChange();
    } finally { this.inFlight = false; }
  }

  /** Re-fetch prices if the list is more than a minute old (called while the tab is open). */
  maybeRefresh() { if (Date.now() - this.loadedAt > 60_000) this.refresh(); }

  render() {
    const top = this.root.scrollTop;
    clear(this.root);
    this.root.append(h("p", { class: "muted small watch-intro" },
      `Names Screening shortlisted. Each is scored from the day it was flagged against the S&P 500 (SPY). Nothing is researched until ${this.state.captain.nickname} sends it.`));
    if (!this.items.length) {
      this.root.append(h("p", { class: "empty" }, "No names yet. Ask Scout for a Gems or Core screen and the shortlist lands here."));
      return;
    }
    for (const w of this.items) this.root.append(this.card(w));
    this.root.scrollTop = top;
  }

  private card(w: Watch): HTMLElement {
    const sent = w.status === "researching";
    const pitchBox = h("div", { class: "pitch" });
    if (this.open.has(w.id)) pitchBox.append(h("pre", { class: "pitch-text" }, this.pitches.get(w.ticker) ?? "Loading…"));
    const toggle = async () => {
      if (this.open.has(w.id)) { this.open.delete(w.id); this.render(); return; }
      this.open.add(w.id);
      this.render();
      if (!this.pitches.has(w.ticker) && w.pitch) {
        const res = await fetch(`/files/coverage/${w.ticker}/${w.pitch}`);
        this.pitches.set(w.ticker, res.ok ? await res.text() : "Couldn't load the pitch.");
        this.render();
      }
    };
    const research = async () => {
      if (this.busy.has(w.id)) return;
      this.busy.add(w.id); this.render();
      await fetch(`/api/watchlist/${w.id}/research`, { method: "POST" });
      this.busy.delete(w.id);
      await this.refresh();
    };
    const drop = async () => { await fetch(`/api/watchlist/${w.id}/drop`, { method: "POST" }); await this.refresh(); };
    const tone = (x: number | null) => (x === null ? "" : x >= 0 ? "up" : "down");
    return h("div", { class: `watch s-${w.status}` },
      h("div", { class: "watch-head" },
        h("span", { class: "watch-ticker" }, w.ticker),
        h("span", { class: "chip" }, w.source),
        sent ? h("span", { class: "chip good" }, "Sent to research") : null,
        h("span", { class: "feed-time" }, timeAgo(w.added))),
      h("p", { class: "watch-thesis" }, w.thesis),
      h("div", { class: "scorecard" },
        h("div", {}, h("span", { class: "muted small" }, "Flagged at"), h("strong", {}, px(w.price_at_add))),
        h("div", {}, h("span", { class: "muted small" }, "Now"), h("strong", {}, px(w.price_now))),
        h("div", {}, h("span", { class: "muted small" }, "Since flagged"), h("strong", { class: tone(w.return) }, pct(w.return))),
        h("div", {}, h("span", { class: "muted small" }, "vs S&P 500"), h("strong", { class: tone(w.vs_spy) }, pct(w.vs_spy)))),
      h("div", { class: "muted small" }, `Added by ${w.added_by_name}`),
      h("div", { class: "row" },
        w.pitch ? h("button", { class: "btn ghost", onclick: toggle }, this.open.has(w.id) ? "Hide pitch" : "Read pitch") : null,
        sent ? null : h("button", { class: "btn", disabled: this.busy.has(w.id), onclick: research },
          this.busy.has(w.id) ? "Sending…" : "Send to research (via Juno)"),
        h("button", { class: "btn ghost danger", onclick: drop }, "Drop")),
      pitchBox);
  }
}
