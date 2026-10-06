// Outbox: what Client Relations has ready for the outside world. Nothing here is posted for the Captain.
import type { OfficeState } from "../state";
import { clear, h, timeAgo } from "./dom";

interface Problem { level: "error" | "warning"; code: string; where: string; msg: string }
interface Issue {
  id: string; title: string; date: string; status: string; words: number; problems: Problem[];
  x_post: string; linkedin_post: string; files: Record<string, string>; approval_id: number | null;
  drafted_by_name: string; revised_by_name: string; updated: number; revisions: number; next_step?: string;
}
interface Deliverable {
  id: string; ticker: string; title: string; date: string; status: string; files: Record<string, string>;
  model_version: number; updated: number;
}
interface Deck {
  id: string; ticker: string; title: string; date: string; status: string; files: Record<string, string>;
  model_version: number; slides: number; figures: number; matched: number; issue: string | null; updated: number;
}
interface Outreach {
  id: string; to_name: string; to_email: string; org: string; segment: string; reason: string; subject: string;
  body: string; words: number; date: string; status: string; problems: Problem[]; drafted_by_name: string;
  updated: number; approval_id: number | null; files: Record<string, string>; next_step?: string;
}
interface OutboxView { issues: Issue[]; deliverables: Deliverable[]; decks?: Deck[]; outreach?: Outreach[]; outreach_from?: string; publisher: string }

const SEGMENT: Record<string, string> = { business: "Business or professional", retail: "Individual investor", inbound: "Asked to hear from us" };

const STATUS: Record<string, string> = { draft: "Draft", finalizing: "Being finalized", blocked: "Checks failing", awaiting: "Waiting for you",
  approved: "Approved: ready", changes: "Changes requested", rejected: "Declined", expired: "Expired (demo tidy-up)" };

export class OutboxPanel {
  view: OutboxView | null = null;
  private inFlight = false;
  private again = false;
  private copied = new Map<string, number>();

  constructor(private root: HTMLElement, private state: OfficeState, private onChange: () => void,
              private openApprovals: () => void) {}

  /** Issues and client files approved and ready for the Captain to post or send. */
  get count(): number {
    if (!this.view) return 0;
    return this.view.issues.filter((i) => i.status === "approved").length
      + (this.view.outreach ?? []).filter((e) => e.status === "approved").length;
  }

  async refresh() {
    if (this.inFlight) { this.again = true; return; }
    this.inFlight = true;
    try {
      const res = await fetch("/api/outbox");
      if (res.ok) { this.view = await res.json(); this.onChange(); }
    } finally {
      this.inFlight = false;
      if (this.again) { this.again = false; this.refresh(); }
    }
  }

  private flash(key: string) {
    this.copied.set(key, Date.now());
    this.onChange();
    setTimeout(() => this.onChange(), 1600);
  }

  private async copyText(key: string, text: string) {
    try { await navigator.clipboard.writeText(text); this.flash(key); } catch { /* clipboard blocked */ }
  }

  /** Copy the article as rich text, so pasting into the Substack editor keeps headings and lists. */
  private async copyArticle(key: string, url: string) {
    try {
      const htmlText = await (await fetch(url)).text();
      const plain = new DOMParser().parseFromString(htmlText, "text/html").body.innerText;
      if ("ClipboardItem" in window) {
        await navigator.clipboard.write([new ClipboardItem({
          "text/html": new Blob([htmlText], { type: "text/html" }), "text/plain": new Blob([plain], { type: "text/plain" }) })]);
      } else await navigator.clipboard.writeText(plain);
      this.flash(key);
    } catch { /* clipboard blocked: the page link still works */ }
  }

  private copyBtn(key: string, label: string, run: () => void): HTMLElement {
    const done = Date.now() - (this.copied.get(key) ?? 0) < 1500;
    return h("button", { class: "btn ghost small-btn", onclick: run }, done ? "Copied" : label);
  }

  render() {
    const top = this.root.scrollTop;
    clear(this.root);
    const captain = this.state.captain.nickname;
    this.root.append(h("p", { class: "muted small audit-intro" },
      `What Client Relations has prepared. Nothing is posted or sent for you: once ${captain} approves an issue, it is marked ready and ${captain} pastes it into Substack.`));
    const v = this.view;
    if (!v) { this.root.append(h("p", { class: "empty" }, "Loading…")); return; }
    this.root.append(h("h4", { class: "section" }, "Newsletter"));
    if (!v.issues.length) this.root.append(h("p", { class: "empty" }, `No issues yet. Ask ${this.state.name("cr_lead")} for this week's newsletter.`));
    for (const i of v.issues) this.root.append(this.issueEl(i));
    this.root.append(h("h4", { class: "section" }, "Outreach"));
    const mails = v.outreach ?? [];
    if (!mails.length) this.root.append(h("p", { class: "empty" }, `No outreach emails yet. Tell ${this.state.name("cr_lead")} who to write to and why.`));
    for (const e of mails) this.root.append(this.outreachEl(e, v.outreach_from ?? ""));
    if (v.decks?.length) {
      this.root.append(h("h4", { class: "section" }, "Slidedecks"));
      for (const d of v.decks) this.root.append(this.deckEl(d));
    }
    if (v.deliverables.length) {
      this.root.append(h("h4", { class: "section" }, "Client memos"));
      for (const d of v.deliverables) this.root.append(this.deliverableEl(d));
    }
    this.root.scrollTop = top;
  }

  private issueEl(i: Issue): HTMLElement {
    const url = (name: string) => `/outbox/${i.files[name]}`;
    const built = !!i.files["issue.html"];
    const errors = i.problems.filter((p) => p.level === "error");
    const warnings = i.problems.filter((p) => p.level === "warning");
    const tone = i.status === "approved" ? " good" : i.status === "blocked" || i.status === "awaiting" ? " warn" : "";
    return h("div", { class: `audit-card outbox s-${i.status}` },
      h("div", { class: "approval-head" },
        h("span", { class: `chip${tone}` }, STATUS[i.status] ?? i.status),
        h("span", { class: "chip" }, `${i.words} words`),
        h("span", { class: "feed-time" }, timeAgo(i.updated))),
      h("div", { class: "outbox-title" }, i.title),
      h("div", { class: "muted small" }, `${i.date} · drafted by ${i.drafted_by_name}${i.revised_by_name !== i.drafted_by_name ? `, edited by ${i.revised_by_name}` : ""}`),
      built ? h("a", { href: url("issue.html"), target: "_blank", rel: "noopener" },
        h("img", { class: "outbox-header", src: `${url("header.png")}?v=${Math.round(i.updated)}`, alt: "Header image" })) : null,
      errors.length ? h("ul", { class: "outbox-problems errors" }, ...errors.map((p) => h("li", {}, `${p.where}: ${p.msg}`))) : null,
      warnings.length ? h("ul", { class: "outbox-problems" }, ...warnings.map((p) => h("li", {}, `${p.where}: ${p.msg}`))) : null,
      !i.problems.length ? h("div", { class: "muted small" }, "Checks passed: only approved numbers, no hype, no advice.") : null,
      i.status === "approved" && i.next_step ? h("div", { class: "outbox-next small" }, i.next_step) : null,
      built ? h("div", { class: "row" },
        h("a", { class: "btn small-btn", href: url("issue.html"), target: "_blank", rel: "noopener" }, "Open the page"),
        this.copyBtn(`a-${i.id}`, "Copy article", () => this.copyArticle(`a-${i.id}`, url("body.html"))),
        this.copyBtn(`x-${i.id}`, "Copy X post", () => this.copyText(`x-${i.id}`, i.x_post)),
        this.copyBtn(`l-${i.id}`, "Copy LinkedIn post", () => this.copyText(`l-${i.id}`, i.linkedin_post)),
        h("a", { class: "btn ghost small-btn", href: url("header.png"), download: `${i.id}-header.png` }, "Download header")) : null,
      i.status === "awaiting" ? h("div", { class: "row" },
        h("button", { class: "btn ghost small-btn", onclick: () => this.openApprovals() }, "Decide in Approvals")) : null,
      built ? h("details", { class: "digest-past" }, h("summary", {}, "Social posts"),
        h("pre", { class: "digest-text" }, `X (${i.x_post.length}/280)\n${i.x_post}\n\nLinkedIn\n${i.linkedin_post}`)) : null);
  }

  private outreachEl(e: Outreach, from: string): HTMLElement {
    const errors = e.problems.filter((p) => p.level === "error");
    const warnings = e.problems.filter((p) => p.level === "warning");
    const tone = e.status === "approved" ? " good" : e.status === "blocked" || e.status === "awaiting" ? " warn" : "";
    const who = e.to_name ? `${e.to_name} <${e.to_email}>` : e.to_email;
    return h("div", { class: `audit-card outbox s-${e.status}` },
      h("div", { class: "approval-head" },
        h("span", { class: `chip${tone}` }, STATUS[e.status] ?? e.status),
        h("span", { class: "chip" }, SEGMENT[e.segment] ?? e.segment),
        h("span", { class: "feed-time" }, timeAgo(e.updated))),
      h("div", { class: "outbox-title" }, e.subject),
      h("div", { class: "muted small" }, `To ${who}${e.org ? ` · ${e.org}` : ""} · ${e.words} words · drafted by ${e.drafted_by_name}`),
      h("div", { class: "muted small" }, `Why: ${e.reason || "not stated"}`),
      errors.length ? h("ul", { class: "outbox-problems errors" }, ...errors.map((p) => h("li", {}, `${p.where}: ${p.msg}`))) : null,
      warnings.length ? h("ul", { class: "outbox-problems" }, ...warnings.map((p) => h("li", {}, `${p.where}: ${p.msg}`))) : null,
      e.status === "approved" && e.next_step ? h("div", { class: "outbox-next small" }, e.next_step) : null,
      e.files["email.html"] ? h("div", { class: "row" },
        h("a", { class: "btn small-btn", href: `/outbox/${e.files["email.html"]}`, target: "_blank", rel: "noopener" }, "Open the email"),
        this.copyBtn(`to-${e.id}`, "Copy To", () => this.copyText(`to-${e.id}`, e.to_email)),
        this.copyBtn(`su-${e.id}`, "Copy subject", () => this.copyText(`su-${e.id}`, e.subject)),
        this.copyBtn(`em-${e.id}`, "Copy email", async () => {
          try { this.copyText(`em-${e.id}`, (await (await fetch(`/outbox/${e.files["email.txt"]}`)).text()).split("\n\n").slice(1).join("\n\n")); } catch { /* blocked */ }
        })) : null,
      e.status === "awaiting" ? h("div", { class: "row" },
        h("button", { class: "btn ghost small-btn", onclick: () => this.openApprovals() }, "Decide in Approvals")) : null,
      h("div", { class: "muted small" }, `From ${from}. Nothing is sent until ${this.state.captain.nickname} approves.`));
  }

  private deckEl(d: Deck): HTMLElement {
    const tone = d.status === "approved" ? " good" : d.status === "awaiting" ? " warn" : "";
    const label: Record<string, string> = { pptx: "Download slidedeck (.pptx)", pdf: "Open PDF", source_map: "Source map" };
    return h("div", { class: `audit-card outbox s-${d.status}` },
      h("div", { class: "approval-head" },
        h("span", { class: `chip${tone}` }, STATUS[d.status] ?? d.status),
        h("span", { class: "chip ticker" }, d.ticker),
        h("span", { class: "feed-time" }, timeAgo(d.updated))),
      h("div", { class: "outbox-title" }, d.title),
      h("div", { class: "muted small" }, `${d.date} · ${d.slides} slides · Quant model v${d.model_version} · ${d.matched} of ${d.figures} figures tied to a source${d.issue ? " · matching note attached" : ""}`),
      h("div", { class: "row" }, ...Object.entries(d.files).map(([kind, path]) =>
        h("a", { class: "btn ghost small-btn", href: `/outbox/${path}`, ...(kind === "pptx" ? { download: "" } : { target: "_blank", rel: "noopener" }) }, label[kind] ?? kind))),
      d.status === "awaiting" ? h("div", { class: "row" },
        h("button", { class: "btn ghost small-btn", onclick: () => this.openApprovals() }, "Decide in Approvals")) : null);
  }

  private deliverableEl(d: Deliverable): HTMLElement {
    const tone = d.status === "approved" ? " good" : d.status === "awaiting" ? " warn" : "";
    return h("div", { class: `audit-card outbox s-${d.status}` },
      h("div", { class: "approval-head" },
        h("span", { class: `chip${tone}` }, STATUS[d.status] ?? d.status),
        h("span", { class: "chip ticker" }, d.ticker),
        h("span", { class: "feed-time" }, timeAgo(d.updated))),
      h("div", { class: "outbox-title" }, d.title),
      h("div", { class: "muted small" }, `${d.date} · figures from Quant model v${d.model_version}`),
      h("div", { class: "row" }, ...Object.entries(d.files).map(([kind, path]) =>
        h("a", { class: "btn ghost small-btn", href: `/outbox/${path}`, download: "" }, `Download .${kind}`))),
      d.status === "awaiting" ? h("div", { class: "row" },
        h("button", { class: "btn ghost small-btn", onclick: () => this.openApprovals() }, "Decide in Approvals")) : null);
  }
}
