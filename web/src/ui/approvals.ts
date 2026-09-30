// Approvals: decisions waiting on the Captain, plus open incidents.
import type { OfficeState } from "../state";
import { avatarImage } from "./panels";
import { clear, h, timeAgo } from "./dom";

export interface Approval {
  id: number; ts: number; kind: string; agent: string; task_id: number | null; title: string;
  summary: string; payload: { ticker?: string; version?: number; attachments?: string[] };
  status: string; decided_ts: number | null; note: string | null;
}

const KIND: Record<string, string> = { brief: "📄 Brief review", model: "📊 Model approval",
  conflict: "⚖️ Thesis conflict", portfolio: "💼 Portfolio entry", newsletter: "📰 Newsletter", other: "📝 Decision" };
const DECIDED: Record<string, string> = { approved: "✅ Approved", changes: "✏️ Changes requested", rejected: "✖️ Declined",
  expired: "⌛ Expired (demo tidy-up)" };

export class ApprovalsPanel {
  items: Approval[] = [];
  private notes = new Map<number, string>();
  constructor(private root: HTMLElement, private state: OfficeState, private onChange: () => void) {}

  get pending(): number { return this.items.filter((a) => a.status === "pending").length + this.state.incidents.length; }

  async refresh() {
    const res = await fetch("/api/approvals");
    if (res.ok) { this.items = await res.json(); this.onChange(); }
  }

  render() {
    clear(this.root);
    const pending = this.items.filter((a) => a.status === "pending").reverse();
    const decided = this.items.filter((a) => a.status !== "pending").reverse().slice(0, 20);
    const incidents = this.state.incidents;
    if (!pending.length && !incidents.length) this.root.append(h("p", { class: "empty" }, "Nothing waiting on you. 🎉"));
    for (const inc of incidents) this.root.append(this.incidentEl(inc));
    for (const a of pending) this.root.append(this.cardEl(a));
    if (decided.length) {
      this.root.append(h("h4", { class: "section" }, "Decided"));
      for (const a of decided) this.root.append(this.cardEl(a));
    }
  }

  private cardEl(a: Approval): HTMLElement {
    const agent = this.state.agents.get(a.agent);
    const meta = [a.payload.ticker, a.payload.version ? `v${a.payload.version}` : null].filter(Boolean).join(" ");
    const note = h("textarea", { rows: 2, placeholder: "Note for the team (optional)",
      oninput: (e: Event) => this.notes.set(a.id, (e.target as HTMLTextAreaElement).value) }, this.notes.get(a.id) ?? "");
    const act = (decision: string) => async () => {
      const res = await fetch(`/api/approvals/${a.id}/decide`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, note: this.notes.get(a.id) ?? null }) });
      if (res.ok) { this.notes.delete(a.id); await this.refresh(); }
    };
    return h("div", { class: `approval k-${a.kind} s-${a.status}` },
      h("div", { class: "approval-head" },
        h("span", { class: "chip" }, KIND[a.kind] ?? a.kind),
        meta ? h("span", { class: "chip ticker" }, meta) : null,
        h("span", { class: "feed-time" }, timeAgo(a.ts))),
      h("h3", {}, a.title),
      h("div", { class: "approval-from" }, agent ? avatarImage(agent.id, agent.avatar, 2) : null,
        h("span", {}, `From ${this.state.name(a.agent)}${agent ? ` · ${agent.role}` : ""}`)),
      h("p", { class: "approval-summary" }, a.summary),
      a.payload.attachments?.length ? h("div", { class: "attachments" }, "📎 ", a.payload.attachments.join(", ")) : null,
      a.status === "pending"
        ? h("div", { class: "approval-actions" }, note, h("div", { class: "row" },
            h("button", { class: "btn", onclick: act("approved") }, "Approve"),
            h("button", { class: "btn ghost", onclick: act("changes") }, "Request changes"),
            h("button", { class: "btn ghost danger", onclick: act("rejected") }, "Decline")))
        : h("div", { class: "decided" }, DECIDED[a.status] ?? a.status, a.note ? ` · "${a.note}"` : ""));
  }

  private incidentEl(inc: any): HTMLElement {
    const agent = inc.agent ? this.state.agents.get(inc.agent) : undefined;
    const ack = async () => { await fetch(`/api/incidents/${inc.id}/resolve`, { method: "POST" }); };
    const unpause = async () => { await fetch(`/api/agents/${inc.agent}/resume`, { method: "POST" }); await ack(); };
    return h("div", { class: "approval incident" },
      h("div", { class: "approval-head" }, h("span", { class: "chip warn" }, "🚨 Incident"), h("span", { class: "chip" }, inc.kind),
        h("span", { class: "feed-time" }, timeAgo(inc.ts))),
      h("p", { class: "approval-summary" }, `${this.state.name(inc.agent)}: ${inc.detail}`),
      h("div", { class: "row" },
        agent?.paused ? h("button", { class: "btn", onclick: unpause }, `Unpause ${agent.nickname}`) : null,
        h("button", { class: "btn ghost", onclick: ack }, "Acknowledge")));
  }
}
