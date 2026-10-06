// Approvals: decisions waiting on the Captain, plus open incidents.
import type { OfficeState } from "../state";
import { avatarImage } from "./panels";
import { clear, fileHref, h, timeAgo } from "./dom";

export interface Approval {
  id: number; ts: number; kind: string; agent: string; task_id: number | null; title: string;
  summary: string;
  payload: { ticker?: string; version?: number; attachments?: string[];
    audit?: { rule: string; severity: string; subject: string; detail: string; status?: string }[] };
  status: string; decided_ts: number | null; note: string | null;
}

const KIND: Record<string, string> = { brief: "📄 Brief review", model: "📊 Model approval",
  conflict: "⚖️ Thesis conflict", portfolio: "💼 Portfolio", newsletter: "📰 Newsletter", deliverable: "📦 Client memo", handoff: "🤝 Ready for Client Relations", deck: "📊 Slidedeck", other: "📝 Decision" };
const DECIDED: Record<string, string> = { approved: "✅ Approved", changes: "✏️ Changes requested", rejected: "✖️ Declined",
  expired: "⌛ Expired (demo tidy-up)" };

export class ApprovalsPanel {
  items: Approval[] = [];
  private notes = new Map<number, string>();
  private errors = new Map<number, string>();
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
      if (res.ok) { this.notes.delete(a.id); this.errors.delete(a.id); await this.refresh(); return; }
      // A refused decision (no price for a paper fill, numbers that changed since the card was
      // filed) must say why: the card stays open and nothing was recorded.
      const why = (await res.json().catch(() => null))?.detail;
      this.errors.set(a.id, typeof why === "string" ? why : "That didn't go through. Nothing was recorded; try again.");
      this.onChange();
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
      this.auditEl(a),
      a.payload.attachments?.length ? h("div", { class: "attachments" }, "📎 ", ...a.payload.attachments.flatMap((f, i) => {
        const href = fileHref(f, a.payload.ticker);
        const page = f.endsWith(".html");   // a web page opens in a new tab; everything else downloads
        const el = href ? h("a", page ? { href, target: "_blank", rel: "noopener" } : { href, download: "" }, f.split("/").pop()!) : h("span", {}, f);
        return i ? [", ", el] : [el];
      })) : null,
      a.status === "pending"
        ? h("div", { class: "approval-actions" }, note,
          this.errors.has(a.id) ? h("div", { class: "approval-error", role: "alert" }, `Not recorded: ${this.errors.get(a.id)}`) : null,
          h("div", { class: "row" },
            h("button", { class: "btn", onclick: act("approved") }, "Approve"),
            h("button", { class: "btn ghost", onclick: act("changes") }, "Request changes"),
            h("button", { class: "btn ghost danger", onclick: act("rejected") }, "Decline")))
        : h("div", { class: "decided" }, DECIDED[a.status] ?? a.status, a.note ? ` · "${a.note}"` : ""));
  }

  /** What the Audit associate's free code checks made of this card (cards from before Phase 6 have none). */
  private auditEl(a: Approval): HTMLElement | null {
    const found = a.payload.audit;
    if (!found) return null;
    if (!found.length) return h("div", { class: "card-audit" }, h("span", { class: "chip good" }, "Audit check: clean"));
    const settled = (f: { status?: string }) => f.status === "cleared" || f.status === "dismissed";
    const all = found.filter((f) => f.severity === "flag");
    const flags = all.filter((f) => !settled(f)).length;
    const label = flags ? `Audit: ${flags} flag${flags === 1 ? "" : "s"}` : all.length ? "Audit: flags cleared" : "Audit: notes only";
    const state: Record<string, string> = { open: "open", reviewing: `${this.state.name("audit_lead")} is reviewing`, cleared: "cleared", upheld: `upheld by ${this.state.name("audit_lead")}`, dismissed: "dismissed by you" };
    return h("div", { class: "card-audit" },
      h("span", { class: `chip${flags ? " warn" : all.length ? " good" : ""}` }, label),
      h("ul", {}, ...found.map((f) => h("li", {}, `${f.subject}: ${f.detail}${f.status ? ` (${state[f.status] ?? f.status})` : ""}`))));
  }

  private incidentEl(inc: any): HTMLElement {
    const agent = inc.agent ? this.state.agents.get(inc.agent) : undefined;
    const ack = async () => { await fetch(`/api/incidents/${inc.id}/resolve`, { method: "POST" }); };
    const unpause = async () => { await fetch(`/api/agents/${inc.agent}/resume`, { method: "POST" }); await ack(); };
    const resumable = ["turn_cap", "tool_loop", "task_cost_cap", "api_credit", "api_auth", "api_rate", "api_busy"].includes(inc.kind);
    const resume = async () => {
      const r = await fetch(`/api/incidents/${inc.id}/resume`, { method: "POST" });
      if (!r.ok) alert((await r.json()).detail ?? "Could not resume.");
    };
    return h("div", { class: "approval incident" },
      h("div", { class: "approval-head" }, h("span", { class: "chip warn" }, "🚨 Incident"), h("span", { class: "chip" }, inc.kind),
        h("span", { class: "feed-time" }, timeAgo(inc.ts))),
      h("p", { class: "approval-summary" }, `${this.state.name(inc.agent)}: ${inc.detail}`),
      h("div", { class: "row" },
        agent?.paused ? h("button", { class: "btn", onclick: unpause }, `Unpause ${agent.nickname}`) : null,
        resumable ? h("button", { class: "btn", onclick: resume }, inc.kind.startsWith("api_") ? "Resume work" : "Resume task") : null,
        h("button", { class: "btn ghost", onclick: ack }, "Acknowledge")));
  }
}
