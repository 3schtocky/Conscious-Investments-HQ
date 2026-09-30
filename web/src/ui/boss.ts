// The Captain's chat bar and tone preview.
//
// Flow: write -> Preview -> compare "You wrote" with "The team reads" (both editable, facts
// check re-runs as you type) -> send the rewrite or your own words. The team only ever sees
// what you send; your original is kept in the log for you.
import type { OfficeState } from "../state";
import { clear, h } from "./dom";

interface Check { ok: boolean; missing: Record<string, string[]>; added_numbers: string[] }
interface Preview { original: string; rewrite: string; engine: string; check: Check }

const LABELS: Record<string, string> = { tickers: "tickers", numbers: "numbers", dates: "dates",
  negations: "don't / never" };

export class BossChannel {
  private to = "office";
  private text = "";
  private preview: Preview | null = null;
  private busy = false;
  private sending = false;
  private status: { text: string; ok: boolean } | null = null;
  private checkTimer = 0;

  constructor(private root: HTMLElement, private state: OfficeState) {}

  /** Pre-select a recipient (e.g. when zoomed in on an agent) unless a draft is under way. */
  setDefaultRecipient(id: string | null) {
    if (!this.preview && !this.text) { this.to = id ?? "office"; this.render(); }
  }

  /** Address the bar to someone explicitly (Start a chat, picking a thread) and focus it. */
  setRecipient(to: string, focus = false) {
    if (this.preview || this.to === to) { if (focus) this.focus(); return; }
    this.to = to;
    this.render();
    if (focus) this.focus();
  }

  focus() { this.root.querySelector<HTMLTextAreaElement>("textarea")?.focus(); }

  render() {
    clear(this.root);
    if (this.preview) {
      this.root.append(this.previewEl(this.preview));
      if (this.status) this.root.append(h("div", { class: `boss-status ${this.status.ok ? "ok" : "err"}` }, this.status.text));
      return;
    }
    const recipients = [["office", "Office (Juno routes it)"], ["all", "Everyone (group text: Stott → All)"],
      ...[...this.state.agents.values()].map((a) => [a.id, `${a.nickname} · ${a.role}`])];
    const select = h("select", { class: "to", title: "Send to", onchange: (e: Event) => { this.to = (e.target as HTMLSelectElement).value; } },
      ...recipients.map(([v, label]) => h("option", { value: v, selected: v === this.to }, label)));
    const area = h("textarea", { rows: 2, placeholder: `Message from ${this.state.captain.nickname}… (⌘↩ to preview)`,
      oninput: (e: Event) => { this.text = (e.target as HTMLTextAreaElement).value; },
      onkeydown: (ev: Event) => {
        const e = ev as KeyboardEvent;
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); this.runPreview(); }
      } }, this.text);
    const btn = h("button", { class: "btn", disabled: this.busy, onclick: () => this.runPreview() }, this.busy ? "Rewriting…" : "Preview");
    this.root.append(h("div", { class: "boss-row" }, h("span", { class: "boss-label" }, "To"), select),
      h("div", { class: "boss-row" }, area, btn),
      this.status ? h("div", { class: `boss-status ${this.status.ok ? "ok" : "err"}` }, this.status.text) : "");
  }

  private async runPreview() {
    if (!this.text.trim() || this.busy) return;
    this.busy = true; this.status = null; this.render();
    try {
      const res = await fetch("/api/captain/preview", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: this.to, text: this.text }) });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail ?? "Preview failed");
      this.preview = body;
    } catch (e) {
      this.status = { text: String((e as Error).message), ok: false };
    } finally { this.busy = false; this.render(); }
  }

  private previewEl(p: Preview): HTMLElement {
    const who = this.to === "office" ? "the office (via Juno)" : this.to === "all" ? "everyone (group text)" : this.state.name(this.to);
    const checkBox = h("div", { class: "tone-check" });
    const renderCheck = (c: Check) => {
      clear(checkBox);
      if (c.ok) { checkBox.append(h("span", { class: "chip good" }, "✓ Every ticker, number, date and 'don't' kept")); return; }
      for (const [k, vals] of Object.entries(c.missing)) {
        checkBox.append(h("span", { class: "chip warn" }, `Missing ${LABELS[k] ?? k}: ${vals.join(", ")}`));
      }
      if (c.added_numbers.length) checkBox.append(h("span", { class: "chip warn" }, `Added numbers: ${c.added_numbers.join(", ")}`));
    };
    renderCheck(p.check);
    const recheck = () => {
      clearTimeout(this.checkTimer);
      this.checkTimer = window.setTimeout(async () => {
        const res = await fetch("/api/captain/check", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ original: p.original, rewrite: p.rewrite }) });
        if (res.ok) { p.check = await res.json(); renderCheck(p.check); }
      }, 250);
    };
    const mine = h("textarea", { rows: 5, oninput: (e: Event) => { p.original = (e.target as HTMLTextAreaElement).value; recheck(); } }, p.original);
    const theirs = h("textarea", { rows: 7, oninput: (e: Event) => { p.rewrite = (e.target as HTMLTextAreaElement).value; recheck(); } }, p.rewrite);
    const engine = p.engine.startsWith("rules") ? `Built-in rewriter${p.engine.includes("(") ? " " + p.engine.slice(5) : ""} · free` : "Rewritten by Juno (Haiku)";
    return h("div", { class: "tone" },
      h("div", { class: "tone-head" }, h("strong", {}, `To ${who}`), h("span", { class: "muted small" }, engine)),
      h("label", { class: "field" }, h("span", {}, "You wrote (kept in your log)"), mine),
      h("label", { class: "field" }, h("span", {}, "The team reads"), theirs),
      checkBox,
      h("div", { class: "row" },
        h("button", { class: "btn", disabled: this.sending, onclick: () => this.send(p.rewrite, p.original) },
          this.sending ? "Sending…" : "Send this version"),
        h("button", { class: "btn ghost", disabled: this.sending, onclick: () => this.send(p.original, null) }, "Send my words"),
        h("button", { class: "btn ghost", onclick: () => { this.text = p.original; this.preview = null; this.runPreview(); } }, "Rewrite again"),
        h("button", { class: "btn ghost", onclick: () => { this.text = p.original; this.preview = null; this.render(); } }, "Cancel")));
  }

  private async send(text: string, original: string | null) {
    if (this.sending) return;   // one click, one message
    this.sending = true; this.status = null; this.render();
    let res: Response, body: any;
    try {
      res = await fetch("/api/captain/send", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: this.to, text, original }) });
      body = await res.json();
    } catch (e) {
      this.sending = false; this.status = { text: `Couldn't reach the office: ${(e as Error).message}`, ok: false }; this.render(); return;
    }
    this.sending = false;
    if (!res.ok) { this.status = { text: body.detail ?? "Couldn't send.", ok: false }; this.render(); return; }
    const who = this.state.name(body.routed_to);
    this.status = { text: this.to === "office" ? `Sent. Juno is routing it.` : this.to === "all" ? "Announced. Replies will come in under Chat › Stott → All." :
      body.delivered === "inbox" ? `Sent. ${who} will see it on their next step.` : `Sent. ${who} is on it.`, ok: true };
    this.text = ""; this.preview = null; this.render();
  }
}
