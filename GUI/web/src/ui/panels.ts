// Desk cards, activity feed, agent zoom view and chat log.
import { ID_PRESET, imageOf, portrait, resolveParts, type AvatarSpec } from "../office/avatar";
import { WING_ACCENT } from "../office/map";
import type { AgentView, Block, OfficeEvent, OfficeState } from "../state";
import { groupName, short } from "../state";
import { clear, fileHref, h, money, plain, timeAgo } from "./dom";

export const WING_ORDER = ["executive", "equity_research", "screening", "quant", "audit", "client_relations"];

export function avatarImage(id: string, spec: AvatarSpec, scale = 3): HTMLElement {
  const img = imageOf(spec);
  if (img) return h("img", { class: "portrait", src: img, alt: "" });
  const c = portrait(resolveParts(spec, ID_PRESET[id] ?? id), scale);
  c.className = "portrait";
  return c;
}

export function modelLabel(modelId: string): string {
  if (modelId.includes("sonnet")) return "Sonnet 5.5";
  if (modelId.includes("haiku")) return "Haiku 4.5";
  if (modelId.includes("opus")) return "Opus";
  return modelId;
}

function statusLabel(a: AgentView, clockedOut: boolean): string {
  if (a.paused) return "Paused";
  if (a.status === "held") return "On hold";
  if (a.status === "working") return a.live?.kind === "thinking" ? "Thinking" : a.live ? "Writing" : "Working";
  return clockedOut ? "Clocked out" : "At desk";
}

// ---- desk cards -----------------------------------------------------------------------------
interface CardRefs { root: HTMLElement; status: HTMLElement; task: HTMLElement; live: HTMLElement; portrait: HTMLElement; spec: string }

export class DeskCards {
  private cards = new Map<string, CardRefs>();

  /** Scroll the strip so the given agents' cards are in view (e.g. on wing focus). */
  reveal(ids: string[]) {
    const first = ids.map((id) => this.cards.get(id)?.root).find(Boolean);
    first?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "start" });
  }
  constructor(private root: HTMLElement, private state: OfficeState, private onPick: (id: string) => void) {}

  render(selected: string | null, wingFilter: string | null) {
    const agents = [...this.state.agents.values()].sort(
      (a, b) => WING_ORDER.indexOf(a.wing) - WING_ORDER.indexOf(b.wing) || (a.tier === "lead" ? -1 : 1));
    for (const a of agents) {
      let c = this.cards.get(a.id);
      const spec = JSON.stringify(a.avatar ?? null);
      if (!c) {
        const status = h("span", { class: "pill" });
        const task = h("div", { class: "card-task" });
        const live = h("div", { class: "card-live" });
        const pic = h("div", { class: "card-pic" });
        const root = h("button", { class: "card", onclick: () => this.onPick(a.id) },
          h("div", { class: "card-head" }, pic,
            h("div", { class: "card-id" }, h("strong", { class: "card-name" }), h("span", { class: "card-role" }))
            , status),
          task, live);
        root.style.setProperty("--accent", WING_ACCENT[a.wing] ?? "#46505e");
        this.root.append(root);
        c = { root, status, task, live, portrait: pic, spec: "" };
        this.cards.set(a.id, c);
      }
      if (c.spec !== spec) { clear(c.portrait); c.portrait.append(avatarImage(a.id, a.avatar, 2)); c.spec = spec; }
      (c.root.querySelector(".card-name") as HTMLElement).textContent = a.nickname;
      (c.root.querySelector(".card-role") as HTMLElement).textContent = `${a.role} · ${modelLabel(a.model_id)}`;
      c.status.textContent = statusLabel(a, this.state.clockedOut);
      c.root.dataset.status = a.paused ? "paused" : a.status;
      c.root.classList.toggle("selected", selected === a.id);
      c.root.classList.toggle("dim", !!wingFilter && a.wing !== wingFilter);
      c.task.textContent = a.task ? a.task.title : "No task";
      const text = a.live ? a.live.text : lastWords(a.blocks);
      const clean = plain(text);
      c.live.textContent = clean ? "…" + clean.slice(-240) : "";
      c.live.classList.toggle("thinking", a.live?.kind === "thinking");
    }
  }
}

function lastWords(blocks: Block[]): string {
  for (let i = blocks.length - 1; i >= 0; i--) {
    const b = blocks[i];
    if (b.kind === "thinking" || b.kind === "text" || b.kind === "report") return b.text;
  }
  return "";
}

// ---- activity feed --------------------------------------------------------------------------
export function renderActivity(root: HTMLElement, state: OfficeState, wing: string | null, onPick: (id: string) => void) {
  // Newest items go on top. Keep the reader's place: if they've scrolled down, anchor the view
  // to what they're reading (offset by whatever was added above); at the top, stay at the top.
  const prevTop = root.scrollTop, prevHeight = root.scrollHeight;
  clear(root);
  const who = (id: string | null) =>
    id && state.agents.has(id) ? h("a", { class: "who", href: "#", onclick: (e: Event) => { e.preventDefault(); onPick(id); } }, state.name(id)) : h("strong", {}, state.name(id));
  const items = state.feed.slice(-150).reverse().filter((ev) => !wing || state.agents.get(ev.agent ?? "")?.wing === wing);
  const visitor = document.body.classList.contains("visitor");
  if (!items.length) root.append(h("p", { class: "empty" }, visitor ? "Quiet right now. Work appears here the moment the office picks it up." : "Quiet so far. Assign work to get the office moving."));
  for (const ev of items) {
    const line = feedLine(ev, state, who);
    if (line) root.append(h("div", { class: `feed-item t-${ev.type}` }, h("span", { class: "feed-time" }, timeAgo(ev.ts)), line));
  }
  root.scrollTop = prevTop > 4 ? prevTop + (root.scrollHeight - prevHeight) : 0;
}

/** ": text" for a feed line, or nothing when the words are hidden (a visitor's stream carries "…"). */
const said = (text: unknown, max: number): string => (!text || text === "…" ? "" : `: ${short(String(text), max)}`);

function feedLine(ev: OfficeEvent, state: OfficeState, who: (id: string | null) => HTMLElement): HTMLElement | null {
  const s = (...kids: (Node | string)[]) => h("span", { class: "feed-text" }, ...kids);
  switch (ev.type) {
    case "task_created": return ev.assigned_by === "captain" ? null : s("📋 ", who(ev.assigned_by), " assigned ", who(ev.agent), `: ${ev.title}`);
    case "task_started": return ev.kind === "assignment" ? s("▶️ ", who(ev.agent), ` started "${ev.title}"`) : null;
    case "task_done": return s("✅ ", who(ev.agent), " finished a task");
    case "delegated": return s("🤝 ", who(ev.agent), " → ", who(ev.to), said(ev.job, 110));
    case "chat":
      if (String(ev.channel).startsWith("group:")) return s("📢 ", who(ev.agent), ` in ${groupName(ev.channel, state)}${said(ev.text, 140)}`);
      return s("💬 ", who(ev.agent), " → ", ...(ev.recipients as string[]).flatMap((r, i) => (i ? [", ", who(r)] : [who(r)])), said(ev.text, 140));
    case "captain_report": return s("📣 ", who(ev.agent), ` → ${state.captain.nickname}: ${short(ev.text, 160)}`);
    case "meeting": return s("🪑 Lobby gathering: ", (ev.participants as string[]).map((p) => state.name(p)).join(", "));
    case "task_paused": return s("⛔ ", who(ev.agent), ` paused (${ev.reason})`);
    case "task_error": return s("⚠️ ", who(ev.agent), ` hit an error: ${short(ev.error, 120)}`);
    case "incident": return s("🚨 Incident · ", who(ev.agent), ` · ${ev.kind}: ${short(String(ev.detail), 120)}`);
    case "office_status": return s(ev.status === "clocked_out" ? "🌙 Daily budget reached: the office clocked out" : "☀️ Office open");
    case "captain_message": return s(ev.to === "all" ? "📢 " : "⭐ ", who("captain"), " → ",
      ev.to === "office" ? h("strong", {}, "the office") : ev.to === "all" ? h("strong", {}, "everyone") : who(ev.to), `: ${short(ev.text, 140)}`);
    case "approval_requested": return s("📝 ", who(ev.agent), ` asks ${state.captain.nickname} to decide: ${ev.title}`);
    case "watchlist_added": return s("👀 ", who(ev.agent), ` added ${ev.ticker} to the watchlist (${ev.source})`);
    case "screen_run": return s("🔎 ", who(ev.agent), ` ran the ${ev.preset} screen (${ev.run})`);
    case "audit_flag": return s(ev.severity === "flag" ? "🔍 Audit flag · " : "🗒️ Audit note · ", who(ev.agent), ` · ${short(`${ev.subject}: ${ev.detail}`, 150)}`);
    case "audit_resolved": return s("🔏 ", ev.by === "captain" ? who("captain") : who(ev.by), ` ${ev.verdict} the finding on ${short(String(ev.subject), 80)}`);
    case "memory_held": return s("🗂️ ", who(ev.agent), ev.kind === "wiki" ? " proposed a wiki entry for your approval" : " has a desk note held for your review");
    case "outbox_draft": return s("✍️ ", who(ev.agent), ` saved a newsletter draft: ${short(String(ev.title), 70)} (${ev.words} words${ev.status === "blocked" ? ", checks failing" : ""})`);
    case "outbox_ready": return s("📬 ", who(ev.agent), ` put "${short(String(ev.title), 70)}" in the Outbox for your approval`);
    case "outbox_status": return ev.status === "approved" ? s("📮 Ready to paste: ", ev.issue ? "the newsletter issue" : "the client memo", " you approved is in the Outbox") : null;
    case "office_hold": return s(ev.held ? "⏸️ " : "▶️ ", who("captain"), ev.held ? " paused the office. Work waits; message anyone to talk one to one." : " resumed the office");
    case "rounds": return s("🧭 ", who(ev.agent), ` did the rounds: ${(ev.wings as unknown[] | undefined)?.length ?? 0} busy wing${(ev.wings as unknown[] | undefined)?.length === 1 ? "" : "s"}${ev.held ? " (office paused)" : ""}`);
    case "audit_digest": return s("🧾 ", who(ev.agent), ` posted the audit digest for ${ev.day}`);
    case "approval_decided": return s("⚖️ ", who("captain"), ` ${ev.decision === "approved" ? "approved" : ev.decision === "changes" ? "asked for changes on" : "declined"} "${ev.title}"`);
  }
  return null;
}

// ---- agent zoom view ------------------------------------------------------------------------
interface Doc { ts: number; kind: string; title: string; text: string; source: string; status?: string;
  note?: string | null; attachments?: string[]; ticker?: string | null; version?: number | null; file?: string }

const DOC_KIND: Record<string, string> = { brief: "📄 Brief", model: "📊 Model", conflict: "⚖️ Conflict",
  portfolio: "💼 Portfolio entry", newsletter: "📰 Newsletter", deliverable: "📦 Client memo", other: "📝 Decision request", report: "📣 Report", result: "📦 Hand-back",
  workbook: "📗 Excel model" };

export class AgentPanel {
  private shownId: string | null = null;
  private view: "log" | "docs" = "log";
  private docs: Doc[] = [];
  private docsFor: string | null = null;
  private docsRoot: HTMLElement | null = null;
  private sub: HTMLElement | null = null;
  private stream: HTMLElement | null = null;
  private liveEl: HTMLElement | null = null;
  private count = -1;
  private header: HTMLElement | null = null;
  /** A visitor on the public site sees who someone is and whether they're working; never
   *  their words, their documents or any control. */
  readOnly = false;

  constructor(private root: HTMLElement, private state: OfficeState,
              private actions: { pause: (id: string) => void; resume: (id: string) => void; chat: (id: string) => void }) {}

  /** Re-fetch the document history (call when this agent files something new). */
  async loadDocs(id: string) {
    if (this.readOnly) return;
    this.docsRequest = id;
    const res = await fetch(`/api/agents/${id}/documents`);
    if (!res.ok || this.docsRequest !== id) return;   // a newer request (another agent) won
    const docs: Doc[] = await res.json();
    const changed = this.docsFor !== id || JSON.stringify(docs) !== JSON.stringify(this.docs);
    this.docs = docs;
    this.docsFor = id;
    if (changed && this.shownId === id) this.renderDocs();
  }
  private docsRequest: string | null = null;

  private renderDocs() {
    if (!this.docsRoot) return;
    clear(this.docsRoot);
    if (this.docsFor !== this.shownId) return;   // never show another agent's documents
    if (!this.docs.length) { this.docsRoot.append(h("p", { class: "empty" }, "No documents yet. Reports, approval requests and hand-backs will appear here; real files (models, memos) arrive in Phase 4.")); return; }
    for (const d of this.docs) {
      const meta = [d.ticker, d.version ? `v${d.version}` : null].filter(Boolean).join(" ");
      this.docsRoot.append(h("div", { class: `doc k-${d.kind}` },
        h("div", { class: "block-label" }, h("span", {}, DOC_KIND[d.kind] ?? d.kind, meta ? ` · ${meta}` : "",
          d.status ? ` · ${d.status}` : ""), h("span", { class: "feed-time" }, timeAgo(d.ts))),
        h("div", { class: "doc-title" }, d.title),
        h("div", { class: "block-text" }, short(d.text, 400)),
        d.file ? h("a", { class: "btn ghost small-btn", href: d.file, download: "" }, "⬇ Download .xlsx") : null,
        d.attachments?.length ? h("div", { class: "attachments" }, "📎 ", ...d.attachments.flatMap((f, i) => {
          const href = fileHref(f, d.ticker);
          const el = href ? h("a", { href, download: "" }, f.split("/").pop()!) : h("span", {}, f);
          return i ? [", ", el] : [el];
        })) : null,
        d.note ? h("div", { class: "muted small" }, `${this.state.captain.nickname}'s note: ${d.note}`) : null));
    }
  }

  render(id: string | null) {
    const a = id ? this.state.agents.get(id) : undefined;
    if (!a) {
      if (this.shownId !== null || !this.root.childElementCount) {
        clear(this.root);
        this.root.append(h("p", { class: "empty" }, "Click anyone in the office (or a desk card) to zoom in on their work."));
        this.shownId = null;
      }
      return;
    }
    if (this.shownId !== a.id) {
      clear(this.root);
      this.shownId = a.id;
      this.count = -1;
      this.header = h("div", { class: "agent-head" });
      this.sub = h("div", { class: "agent-sub" });
      this.stream = h("div", { class: "stream" });
      this.docsRoot = h("div", { class: "stream docs" });
      this.liveEl = h("div", { class: "block live" });
      this.root.append(this.header, this.sub, this.stream, this.docsRoot);
      if (this.docsFor !== a.id) { this.docs = []; this.loadDocs(a.id); }
    }
    this.renderHeader(a);
    if (this.readOnly) {
      clear(this.sub!); clear(this.stream!);
      this.docsRoot!.classList.add("hidden");
      this.stream!.append(h("p", { class: "empty" }, "What the team says and thinks stays inside the office. You can watch who is working here, and read finished work in the other tabs."));
      return;
    }
    this.renderSub(a);
    this.stream!.classList.toggle("hidden", this.view !== "log");
    this.docsRoot!.classList.toggle("hidden", this.view !== "docs");
    if (this.view === "docs") {   // redrawn only when its contents change (see loadDocs)
      if (this.docsFor !== a.id && this.docsRoot!.childElementCount) clear(this.docsRoot!);
      return;
    }
    const nearBottom = this.stream!.scrollHeight - this.stream!.scrollTop - this.stream!.clientHeight < 60;
    if (a.blocks.length !== this.count) {
      clear(this.stream!);
      for (const b of a.blocks.slice(-80)) this.stream!.append(blockEl(b));
      this.stream!.append(this.liveEl!);
      this.count = a.blocks.length;
    }
    this.liveEl!.textContent = a.live ? a.live.text : "";
    this.liveEl!.className = `block live ${a.live ? `b-${a.live.kind}` : "hidden"}`;
    if (nearBottom) this.stream!.scrollTop = this.stream!.scrollHeight;
  }

  private renderSub(a: AgentView) {
    const sub = this.sub!;
    clear(sub);
    const seg = (v: "log" | "docs", label: string) => h("button", { class: `seg${this.view === v ? " active" : ""}`,
      onclick: () => {
        this.view = v; this.count = -1;
        if (v === "docs") { this.renderDocs(); this.loadDocs(a.id); }
        this.render(a.id);
      } }, label);
    sub.append(
      h("button", { class: "btn start-chat", onclick: () => this.actions.chat(a.id) }, `💬 Start a chat with ${a.nickname}`),
      h("div", { class: "segs" }, seg("log", "Log history"), seg("docs", `Document history${this.docsFor === a.id && this.docs.length ? ` (${this.docs.length})` : ""}`)));
  }

  private renderHeader(a: AgentView) {
    const hd = this.header!;
    clear(hd);
    hd.style.setProperty("--accent", WING_ACCENT[a.wing] ?? "#46505e");
    const btn = this.readOnly ? null : a.paused
      ? h("button", { class: "btn", onclick: () => this.actions.resume(a.id) }, "▶ Unpause")
      : h("button", { class: "btn ghost", onclick: () => this.actions.pause(a.id) }, "⏸ Pause");
    hd.append(
      h("div", { class: "agent-id" }, avatarImage(a.id, a.avatar, 4),
        h("div", {},
          h("h2", {}, a.nickname),
          h("div", { class: "muted" }, `${a.role} · ${this.state.wings[a.wing] ?? a.wing}`),
          h("div", { class: "badges" },
            h("span", { class: "pill" }, statusLabel(a, this.state.clockedOut)),
            h("span", { class: "pill model" }, modelLabel(a.model_id)),
            this.readOnly ? null : h("span", { class: "pill" }, `${money(a.spend)} today`)))),
      h("div", { class: "agent-task" }, h("span", { class: "muted" }, "Current task: "), a.task ? a.task.title : "none"),
      h("p", { class: "persona" }, a.persona));
    if (btn) hd.append(btn);
  }
}

function blockEl(b: Block): HTMLElement {
  const label: Record<Block["kind"], string> = {
    thinking: "💭 Thinking", text: "💬 Says", tool: "🔧 Tool", result: "↩ Result", chat: "✉️ Message",
    delegated: "🤝 Hand-off", report: "📣 To the Captain", task: "📋 Task", alert: "⚠️ Alert",
  };
  return h("div", { class: `block b-${b.kind}${b.error ? " error" : ""}` },
    h("div", { class: "block-label" }, label[b.kind], h("span", { class: "feed-time" }, timeAgo(b.ts))),
    h("div", { class: "block-text" }, b.text));
}

// ---- chat -----------------------------------------------------------------------------------
export class ChatPanel {
  channel: string | null = null;
  /** Called with the chat bar recipient that matches the open channel ("all", an agent id…). */
  onChannel: (recipient: string | null) => void = () => {};
  constructor(private root: HTMLElement, private state: OfficeState) {}

  /** Open a channel even if it has no messages yet (e.g. a new 1-1 with an agent). */
  open(channel: string) { this.channel = channel; this.pinned = channel; }
  private pinned: string | null = null;
  private notified: string | null = null;

  static recipientFor(channel: string | null): string | null {
    if (!channel) return null;
    if (channel === "group:stott") return "all";
    if (channel === "captain:office") return "office";
    const m = channel.match(/^dm:captain\|(.+)$/);
    return m ? m[1] : null;
  }

  channelName(ch: string): string {
    if (ch.startsWith("group:")) return `📢 ${groupName(ch, this.state)}`;
    if (ch === "lobby") return "🪑 Lobby";
    if (ch === "captain:office") return `⭐ ${this.state.captain.nickname} → Office`;
    if (ch.startsWith("wing:")) return `🏢 ${this.state.wings[ch.slice(5)] ?? ch.slice(5)} wing`;
    if (ch.startsWith("dm:")) {
      const [a, b] = ch.slice(3).split("|");
      const star = a === "captain" || b === "captain" ? "⭐ " : "";
      return `${star}${this.state.name(a)} ↔ ${this.state.name(b)}`;
    }
    return ch;
  }

  render(agentFilter: string | null) {
    // Follow new messages only if the reader is already at the bottom of the same channel.
    const old = this.root.querySelector<HTMLElement>(".messages");
    const oldChannel = this.channel;
    const atBottom = !old || old.scrollHeight - old.scrollTop - old.clientHeight < 40;
    const oldTop = old?.scrollTop ?? 0;
    clear(this.root);
    const latest = new Map<string, number>();
    for (const m of this.state.chat) latest.set(m.channel, Math.max(latest.get(m.channel) ?? 0, m.ts));
    let channels = [...latest.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c)
      .filter((c) => !c.startsWith("group:"));
    if (agentFilter) channels = channels.filter((c) => c.includes(agentFilter) || c === "lobby");
    if (this.pinned && !channels.includes(this.pinned) && !this.pinned.startsWith("group:")) channels.unshift(this.pinned);
    channels = ["group:stott", "group:juno", ...channels];   // the two group texts, always on top
    if (!this.channel || !channels.includes(this.channel)) this.channel = channels[2] ?? channels[0];
    if (this.channel !== this.notified) {   // only when the open thread actually changes
      this.notified = this.channel;
      this.onChannel(ChatPanel.recipientFor(this.channel));
    }
    const list = h("div", { class: "channels" }, ...channels.map((c) =>
      h("button", { class: `channel${c === this.channel ? " active" : ""}`, onclick: () => { this.channel = c; this.render(agentFilter); } }, this.channelName(c))));
    const msgs = h("div", { class: "messages" });
    const thread = this.state.chat.filter((x) => x.channel === this.channel);
    if (!thread.length) msgs.append(h("p", { class: "empty" }, this.channel?.startsWith("group:")
      ? "No announcements yet. Pick “Everyone” in the chat bar to post one; every colleague replies here."
      : this.channel?.startsWith("dm:captain|") ? `Start your 1-1 with ${this.state.name(this.channel.split("|")[1])} in the chat bar below.`
      : "No conversations yet."));
    for (const m of this.state.chat.filter((x) => x.channel === this.channel).slice(-120)) {
      const mine = m.sender === "captain";
      msgs.append(h("div", { class: `msg${mine ? " mine" : ""}` },
        h("div", { class: "msg-meta" }, h("strong", {}, this.state.name(m.sender)), h("span", { class: "feed-time" }, timeAgo(m.ts))),
        h("div", { class: "msg-text" }, m.text),
        m.original ? h("details", { class: "original" }, h("summary", {}, "Your original words (only you see this)"),
          h("div", { class: "msg-text" }, m.original)) : null));
    }
    this.root.append(list, msgs);
    msgs.scrollTop = atBottom || this.channel !== oldChannel ? msgs.scrollHeight : oldTop;
  }
}
