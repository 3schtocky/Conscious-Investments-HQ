// Audit: what the associate's free code checks found, what the lead ruled, memory writes and the daily digest.
import type { OfficeState } from "../state";
import { clear, h, money, timeAgo } from "./dom";

interface Finding {
  id: number; ts: number; agent: string | null; name: string; task_id: number | null; rule: string;
  severity: "flag" | "note"; subject: string; detail: string; status: string;
  resolved_by_name: string | null; note: string | null;
}
interface MemoryWrite {
  id: number; ts: number; agent: string; name: string; kind: "desk" | "wiki"; text: string;
  reasons: string[]; status: string;
}
interface Digest {
  day: string; text: string; open_items: string[]; tool_errors: number;
  spend: { total: number; cap: number };
  tasks: { started: number; done: number; paused: number; error: number };
  findings: { flags: number; notes: number; open: number };
  memory: { saved: number; held: number };
}
interface AuditView {
  findings: Finding[]; memory: MemoryWrite[]; digest: Digest;
  digests: { day: string; ts: number; text: string }[];
  paused: { agent: string; name: string; by: string; by_name: string; reason: string; ts: number }[];
  api_available: boolean;
}

export const RULE: Record<string, string> = {
  unapproved_figures: "Unapproved figure", verify_left: "[VERIFY] left", valuation_in_pitch: "Valuation in a pitch",
  missing_sources: "No sources file", tool_errors: "Tool failures", task_spend: "Heavy spend",
};
const statusLabel = (status: string, reviewer: string): string => ({ open: "Open", reviewing: `${reviewer} is reviewing`,
  cleared: "Cleared", upheld: "Upheld", dismissed: "Dismissed by you" } as Record<string, string>)[status] ?? status;
const MEMORY: Record<string, string> = { saved: "Saved", pending: "Waiting for you", approved: "Approved",
  rejected: "Rejected", removed: "Removed", expired: "Expired (demo tidy-up)" };

export class AuditPanel {
  view: AuditView | null = null;
  private loadedAt = 0;
  private inFlight = false;
  private again = false;
  private error = new Map<string, string>();

  constructor(private root: HTMLElement, private state: OfficeState, private onChange: () => void) {}

  /** Items waiting on the Captain: flags nobody has reviewed and memory writes held for him. */
  get count(): number {
    if (!this.view) return this.state.audit.open_flags + this.state.audit.memory_pending;
    return this.view.findings.filter((f) => f.severity === "flag" && f.status === "open").length
      + this.view.memory.filter((m) => m.status === "pending").length;
  }

  async refresh() {
    if (this.inFlight) { this.again = true; return; }   // never drop the newest change
    this.inFlight = true;
    this.loadedAt = Date.now();
    try {
      const res = await fetch("/api/audit");
      if (res.ok) { this.view = await res.json(); this.onChange(); }
    } finally {
      this.inFlight = false;
      if (this.again) { this.again = false; this.refresh(); }
    }
  }

  /** Keep today's numbers fresh while the tab is open. */
  maybeRefresh() { if (Date.now() - this.loadedAt > 30_000) this.refresh(); }

  private async post(key: string, path: string, body?: unknown) {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    if (res.ok) this.error.delete(key);
    else this.error.set(key, (await res.json().catch(() => null))?.detail ?? "That didn't work.");
    await this.refresh();
    this.onChange();
  }

  render() {
    const top = this.root.scrollTop;
    clear(this.root);
    const v = this.view;
    const captain = this.state.captain.nickname;
    this.root.append(h("p", { class: "muted small audit-intro" },
      `${this.state.name("audit_associate")}'s checks run as plain code on every approval card and finished assignment, at no API cost. ${this.state.name("audit_lead")} is called in only when a check raises a flag. Only ${captain} unpauses anyone.`));
    if (!v) { this.root.append(h("p", { class: "empty" }, "Loading…")); return; }

    const flags = v.findings.filter((f) => f.severity === "flag" && (f.status === "open" || f.status === "reviewing")).reverse();
    const held = v.memory.filter((m) => m.status === "pending").reverse();
    this.root.append(h("h4", { class: "section" }, "Needs you"));
    if (!v.paused.length && !flags.length && !held.length) this.root.append(h("p", { class: "empty" }, "Nothing from Audit is waiting on you."));
    for (const p of v.paused) this.root.append(this.pausedEl(p));
    for (const f of flags) this.root.append(this.findingEl(f, v.api_available));
    for (const m of held) this.root.append(this.memoryEl(m));

    this.root.append(h("h4", { class: "section" }, `Today (${v.digest.day})`), this.digestEl(v.digest));

    const rest = v.findings.filter((f) => !flags.includes(f)).reverse().slice(0, 25);
    if (rest.length) {
      this.root.append(h("h4", { class: "section" }, "Rulings and notes"));
      for (const f of rest) this.root.append(this.findingEl(f, v.api_available));
    }
    const log = v.memory.filter((m) => m.status !== "pending").reverse().slice(0, 25);
    if (log.length) {
      this.root.append(h("h4", { class: "section" }, "Memory log"));
      for (const m of log) this.root.append(this.memoryEl(m));
    }
    if (v.digests.length) {
      this.root.append(h("h4", { class: "section" }, "Past digests"));
      for (const d of v.digests) {
        this.root.append(h("details", { class: "digest-past" }, h("summary", {}, d.day), h("pre", { class: "digest-text" }, d.text)));
      }
    }
    this.root.scrollTop = top;
  }

  private errorEl(key: string): HTMLElement | null {
    const msg = this.error.get(key);
    return msg ? h("div", { class: "audit-error small" }, msg) : null;
  }

  private pausedEl(p: AuditView["paused"][number]): HTMLElement {
    const key = `pause-${p.agent}`;
    return h("div", { class: "audit-card paused" },
      h("div", { class: "approval-head" }, h("span", { class: "chip warn" }, "Paused"),
        h("span", { class: "chip" }, `by ${p.by_name}`), h("span", { class: "feed-time" }, timeAgo(p.ts))),
      h("p", { class: "approval-summary" }, `${p.name}: ${p.reason}`),
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: () => this.post(key, `/api/agents/${p.agent}/resume`) }, `Unpause ${p.name}`)),
      this.errorEl(key));
  }

  private findingEl(f: Finding, apiOn: boolean): HTMLElement {
    const key = `finding-${f.id}`;
    const open = f.status === "open";
    const live = open || f.status === "reviewing";
    return h("div", { class: `audit-card ${f.severity} s-${f.status}` },
      h("div", { class: "approval-head" },
        h("span", { class: `chip${f.severity === "flag" && live ? " warn" : ""}` }, f.severity === "flag" ? "Flag" : "Note"),
        h("span", { class: "chip" }, RULE[f.rule] ?? f.rule),
        h("span", { class: `chip${f.status === "cleared" ? " good" : ""}` }, statusLabel(f.status, this.state.name("audit_lead"))),
        h("span", { class: "feed-time" }, timeAgo(f.ts))),
      h("div", { class: "audit-subject" }, `${f.name} · ${f.subject}`),
      h("p", { class: "approval-summary" }, f.detail),
      f.note ? h("div", { class: "muted small" }, `${f.resolved_by_name ?? "Audit"}: ${f.note}`) : null,
      live ? h("div", { class: "row" },   // a review that stalls (API off, a failed task) can still be dismissed
        !open ? null : apiOn ? h("button", { class: "btn", onclick: () => this.post(key, `/api/audit/findings/${f.id}/review`) }, `Ask ${this.state.name("audit_lead")} to review`)
          : h("span", { class: "muted small" }, `${this.state.name("audit_lead")} can review this once the API is on.`),
        h("button", { class: "btn ghost", onclick: () => this.post(key, `/api/audit/findings/${f.id}/dismiss`) }, "Dismiss")) : null,
      this.errorEl(key));
  }

  private memoryEl(m: MemoryWrite): HTMLElement {
    const key = `memory-${m.id}`;
    const pending = m.status === "pending";
    const what = m.kind === "wiki" ? "Office wiki entry (everyone reads it)" : `${m.name}'s desk note`;
    const decide = (decision: string) => () => this.post(key, `/api/memory/${m.id}/decide`, { decision });
    return h("div", { class: `audit-card memory k-${m.kind} s-${m.status}` },
      h("div", { class: "approval-head" },
        h("span", { class: "chip" }, m.kind === "wiki" ? "Wiki proposal" : "Desk note"),
        h("span", { class: `chip${pending ? " warn" : ""}` }, MEMORY[m.status] ?? m.status),
        h("span", { class: "feed-time" }, timeAgo(m.ts))),
      h("div", { class: "audit-subject" }, pending && m.kind === "wiki" ? `${m.name} proposes · ${what}` : what),
      h("p", { class: "approval-summary" }, m.text),
      pending && m.kind === "wiki" ? h("div", { class: "muted small" }, `Every wiki change waits for ${this.state.captain.nickname}.`) : null,
      pending && m.reasons.length ? h("div", { class: m.kind === "wiki" ? "audit-error small" : "muted small" },
        m.kind === "wiki" ? `Code screen: it ${m.reasons.join(" and ")}.` : `Held because it ${m.reasons.join(" and ")}.`) : null,
      pending ? h("div", { class: "row" },
        h("button", { class: "btn", onclick: decide("approved") }, m.kind === "wiki" ? "Add to the wiki" : "Save the note"),
        h("button", { class: "btn ghost danger", onclick: decide("rejected") }, "Reject")) : null,
      m.status === "saved" || m.status === "approved" ? h("div", { class: "row" },
        h("button", { class: "btn ghost small-btn", onclick: () => this.post(key, `/api/memory/${m.id}/remove`) }, "Remove from memory")) : null,
      this.errorEl(key));
  }

  private digestEl(d: Digest): HTMLElement {
    const tile = (label: string, value: string, tone = "") =>
      h("div", {}, h("span", { class: "muted small" }, label), h("strong", { class: tone }, value));
    return h("div", { class: "audit-card digest" },
      h("div", { class: "scorecard" },
        tile("Spend", `${money(d.spend.total)} of ${money(d.spend.cap)}`),
        tile("Tasks done", `${d.tasks.done} of ${d.tasks.started}`, d.tasks.error || d.tasks.paused ? "down" : ""),
        tile("Flags", `${d.findings.flags} (${d.findings.open} open)`, d.findings.open ? "down" : ""),
        tile("Tool failures", String(d.tool_errors))),
      d.open_items.length
        ? h("ul", { class: "digest-open" }, ...d.open_items.map((x) => h("li", {}, x)))
        : h("div", { class: "muted small" }, "Nothing needs you."),
      h("details", { class: "digest-past" }, h("summary", {}, "Read the full digest (built by code, no API cost)"),
        h("pre", { class: "digest-text" }, d.text)));
  }
}
