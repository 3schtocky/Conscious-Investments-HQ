// Desk cards, activity feed, agent zoom view and chat log.
import { ID_PRESET, imageOf, portrait, resolveParts, type AvatarSpec } from "../office/avatar";
import { WING_ACCENT } from "../office/map";
import type { AgentView, Block, OfficeEvent, OfficeState } from "../state";
import { short } from "../state";
import { clear, h, money, plain, timeAgo } from "./dom";

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
  if (!items.length) root.append(h("p", { class: "empty" }, "Quiet so far. Assign work to get the office moving."));
  for (const ev of items) {
    const line = feedLine(ev, state, who);
    if (line) root.append(h("div", { class: `feed-item t-${ev.type}` }, h("span", { class: "feed-time" }, timeAgo(ev.ts)), line));
  }
  root.scrollTop = prevTop > 4 ? prevTop + (root.scrollHeight - prevHeight) : 0;
}

function feedLine(ev: OfficeEvent, state: OfficeState, who: (id: string | null) => HTMLElement): HTMLElement | null {
  const s = (...kids: (Node | string)[]) => h("span", { class: "feed-text" }, ...kids);
  switch (ev.type) {
    case "task_created": return ev.assigned_by === "captain" ? null : s("📋 ", who(ev.assigned_by), " assigned ", who(ev.agent), `: ${ev.title}`);
    case "task_started": return ev.kind === "assignment" ? s("▶️ ", who(ev.agent), ` started "${ev.title}"`) : null;
    case "task_done": return s("✅ ", who(ev.agent), " finished a task");
    case "delegated": return s("🤝 ", who(ev.agent), " → ", who(ev.to), `: ${short(ev.job, 110)}`);
    case "chat": return s("💬 ", who(ev.agent), " → ", ...(ev.recipients as string[]).flatMap((r, i) => (i ? [", ", who(r)] : [who(r)])), `: ${short(ev.text, 140)}`);
    case "captain_report": return s("📣 ", who(ev.agent), ` → ${state.captain.nickname}: ${short(ev.text, 160)}`);
    case "meeting": return s("🪑 Lobby gathering: ", (ev.participants as string[]).map((p) => state.name(p)).join(", "));
    case "task_paused": return s("⛔ ", who(ev.agent), ` paused (${ev.reason})`);
    case "task_error": return s("⚠️ ", who(ev.agent), ` hit an error: ${short(ev.error, 120)}`);
    case "incident": return s("🚨 Incident · ", who(ev.agent), ` · ${ev.kind}: ${short(String(ev.detail), 120)}`);
    case "office_status": return s(ev.status === "clocked_out" ? "🌙 Daily budget reached: the office clocked out" : "☀️ Office open");
    case "captain_message": return s("⭐ ", who("captain"), " → ", ev.to === "office" ? h("strong", {}, "the office") : who(ev.to), `: ${short(ev.text, 140)}`);
    case "approval_requested": return s("📝 ", who(ev.agent), ` asks ${state.captain.nickname} to decide: ${ev.title}`);
    case "approval_decided": return s("⚖️ ", who("captain"), ` ${ev.decision === "approved" ? "approved" : ev.decision === "changes" ? "asked for changes on" : "declined"} "${ev.title}"`);
  }
  return null;
}

// ---- agent zoom view ------------------------------------------------------------------------
export class AgentPanel {
  private shownId: string | null = null;
  private stream: HTMLElement | null = null;
  private liveEl: HTMLElement | null = null;
  private count = -1;
  private header: HTMLElement | null = null;

  constructor(private root: HTMLElement, private state: OfficeState,
              private actions: { pause: (id: string) => void; resume: (id: string) => void }) {}

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
      this.stream = h("div", { class: "stream" });
      this.liveEl = h("div", { class: "block live" });
      this.root.append(this.header, this.stream);
    }
    this.renderHeader(a);
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

  private renderHeader(a: AgentView) {
    const hd = this.header!;
    clear(hd);
    hd.style.setProperty("--accent", WING_ACCENT[a.wing] ?? "#46505e");
    const btn = a.paused
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
            h("span", { class: "pill" }, `${money(a.spend)} today`)))),
      h("div", { class: "agent-task" }, h("span", { class: "muted" }, "Current task: "), a.task ? a.task.title : "none"),
      h("p", { class: "persona" }, a.persona),
      btn);
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
  constructor(private root: HTMLElement, private state: OfficeState) {}

  channelName(ch: string): string {
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
    let channels = [...latest.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
    if (agentFilter) channels = channels.filter((c) => c.includes(agentFilter) || c === "lobby");
    if (!this.channel || !channels.includes(this.channel)) this.channel = channels[0] ?? null;
    const list = h("div", { class: "channels" }, ...channels.map((c) =>
      h("button", { class: `channel${c === this.channel ? " active" : ""}`, onclick: () => { this.channel = c; this.render(agentFilter); } }, this.channelName(c))));
    const msgs = h("div", { class: "messages" });
    if (!this.channel) msgs.append(h("p", { class: "empty" }, "No conversations yet."));
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
