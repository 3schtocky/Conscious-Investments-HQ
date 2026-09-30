// Client-side office state: the /api/state snapshot plus live WebSocket events.
import type { AvatarSpec } from "./office/avatar";

export interface OfficeEvent {
  id: number | null;
  ts: number;
  type: string;
  agent: string | null;
  task_id: number | null;
  [key: string]: any;
}

export interface Block {
  kind: "thinking" | "text" | "tool" | "result" | "chat" | "delegated" | "report" | "task" | "alert";
  text: string;
  ts: number;
  task_id: number | null;
  error?: boolean;
}

export interface AgentView {
  id: string;
  nickname: string;
  wing: string;
  role: string;
  persona: string;
  model: string;
  model_id: string;
  tier: string;
  avatar: AvatarSpec;
  status: string;
  paused: boolean;
  task: { id: number; title: string } | null;
  live: { kind: "thinking" | "text"; text: string } | null;   // the block streaming right now
  blocks: Block[];                                               // finished history, newest last
  spend: number;
}

export interface ChatMsg {
  id: number;
  ts: number;
  channel: string;
  sender: string;
  recipients: string[];
  text: string;
  task_id: number | null;
  original?: string | null;
}

export interface Snapshot {
  office: { clocked_out: boolean; day: string; demo: boolean; max_concurrent: number };
  wings: Record<string, string>;
  captain: { nickname: string; avatar?: AvatarSpec };
  models: Record<string, string>;
  agents: any[];
  spend: { today: number; cap: number; by_agent: Record<string, number>; by_model: Record<string, number> };
  events: OfficeEvent[];
  chat: ChatMsg[];
  incidents: any[];
}

const MAX_BLOCKS = 200;
type Listener = (ev: OfficeEvent | null) => void;

export class OfficeState {
  demo = false;
  clockedOut = false;
  wings: Record<string, string> = {};
  captain: { nickname: string; avatar?: AvatarSpec } = { nickname: "Captain" };
  models: Record<string, string> = {};
  agents = new Map<string, AgentView>();
  chat: ChatMsg[] = [];
  feed: OfficeEvent[] = [];   // notable events for the activity feed
  spend = { today: 0, cap: 10, byModel: {} as Record<string, number> };
  incidents: any[] = [];
  private listeners = new Set<Listener>();

  load(s: Snapshot) {
    this.demo = s.office.demo;
    this.clockedOut = s.office.clocked_out;
    this.wings = s.wings;
    this.captain = s.captain;
    this.models = s.models;
    this.spend = { today: s.spend.today, cap: s.spend.cap, byModel: { ...s.spend.by_model } };
    this.incidents = s.incidents;
    const prev = this.agents;
    this.agents = new Map();
    for (const a of s.agents) {
      const old = prev.get(a.id);
      this.agents.set(a.id, {
        ...a, live: old?.live ?? null, blocks: old?.blocks ?? [], spend: s.spend.by_agent[a.id] ?? 0,
      });
    }
    this.chat = s.chat;
    if (!prev.size) {
      this.feed = [];
      for (const ev of s.events) this.apply(ev, false);
    }
    this.emit(null);
  }

  name(id: string | null | undefined): string {
    if (!id) return "Office";
    if (id === "captain") return this.captain.nickname;
    return this.agents.get(id)?.nickname ?? id;
  }

  on(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(ev: OfficeEvent | null) {
    for (const fn of this.listeners) fn(ev);
  }

  private push(a: AgentView | undefined, b: Block) {
    if (!a) return;
    a.blocks.push(b);
    if (a.blocks.length > MAX_BLOCKS) a.blocks.splice(0, a.blocks.length - MAX_BLOCKS);
  }

  /** Apply one event. `live` = arrived over the socket. A snapshot replay (live = false) only
   *  rebuilds history (blocks, feed); status, tasks and spend come from the snapshot itself. */
  apply(ev: OfficeEvent, live = true) {
    const a = ev.agent ? this.agents.get(ev.agent) : undefined;
    const b = (kind: Block["kind"], text: string, error = false) =>
      this.push(a, { kind, text, ts: ev.ts, task_id: ev.task_id, error });
    switch (ev.type) {
      case "thinking_delta":
      case "text_delta": {
        if (!a) break;
        const kind = ev.type === "thinking_delta" ? "thinking" : "text";
        if (!a.live || a.live.kind !== kind) a.live = { kind, text: "" };
        a.live.text += ev.text;
        break;
      }
      case "thinking":
      case "text":
        if (a) a.live = null;
        b(ev.type, ev.text);
        break;
      case "tool_call":
        b("tool", `${ev.tool}(${short(JSON.stringify(ev.input))})`);
        break;
      case "tool_result":
        if (ev.is_error) b("result", ev.text, true);
        break;
      case "chat": {
        const group = String(ev.channel ?? "").startsWith("group:");
        if (group) {
          b("chat", `In ${groupName(ev.channel, this)}: ${ev.text}`);   // no copy into 10 other logs
        } else {
          const to = (ev.recipients as string[]).map((r) => this.name(r)).join(", ");
          b("chat", `→ ${to}: ${ev.text}`);
          for (const r of ev.recipients as string[]) {
            this.push(this.agents.get(r), { kind: "chat", text: `${this.name(ev.agent)}: ${ev.text}`, ts: ev.ts, task_id: null });
          }
        }
        if (ev.agent === "captain") break;   // the Captain's own posts arrive as captain_message
        if (live) this.chat.push({ id: ev.id ?? Date.now(), ts: ev.ts, channel: ev.channel, sender: ev.agent!,
          recipients: ev.recipients, text: ev.text, task_id: ev.task_id });
        break;
      }
      case "delegated":
        b("delegated", `Handed a job to ${this.name(ev.to)}: ${ev.job}`);
        break;
      case "captain_report":
        b("report", ev.text);
        if (live) this.chat.push({ id: ev.id ?? Date.now(), ts: ev.ts, channel: `dm:captain|${ev.agent}`,
          sender: ev.agent!, recipients: ["captain"], text: ev.text, task_id: ev.task_id });
        break;
      case "captain_message":
        if (live) this.chat.push({ id: ev.id ?? Date.now(), ts: ev.ts,
          channel: ev.to === "office" ? "captain:office" : ev.to === "all" ? "group:stott" : `dm:captain|${ev.to}`,
          sender: "captain", recipients: [ev.agent!], text: ev.text, task_id: ev.task_id,
          original: ev.original ?? null });
        break;
      case "task_created":
        if (live && ev.assigned_by === "captain" && !this.chat.some((m) => m.task_id === ev.task_id && m.sender === "captain")) {
          this.chat.push({ id: ev.id ?? Date.now(), ts: ev.ts, channel: `dm:captain|${ev.agent}`,
            sender: "captain", recipients: [ev.agent!], text: ev.title, task_id: ev.task_id });
        }
        break;
      case "task_started":
        if (a && live) a.task = { id: ev.task_id!, title: ev.title };
        b("task", `Started: ${ev.title}`);
        break;
      case "task_done":
        b("task", "Finished the task.");
        break;
      case "task_paused":
        b("alert", `Task paused (${ev.reason}): ${ev.detail ?? ""}`, true);
        break;
      case "task_error":
        b("alert", `Task error: ${ev.error}`, true);
        break;
      case "status":
        if (a && live) {
          a.status = ev.status;
          a.paused = ev.status === "paused";
          if (ev.status === "idle") { a.task = null; a.live = null; }
        }
        break;
      case "spend":
        if (!live) break;
        this.spend.today = ev.spent_today;
        this.spend.cap = ev.cap;
        this.spend.byModel[ev.model] = (this.spend.byModel[ev.model] ?? 0) + ev.cost;
        if (a) a.spend += ev.cost;
        break;
      case "office_status":
        if (live) this.clockedOut = ev.status === "clocked_out";
        break;
      case "incident_resolved":
        this.incidents = this.incidents.filter((i) => i.id !== ev.incident);
        break;
      case "incident":
        if (live) this.incidents.push({ ...ev, id: ev.incident_id });
        b("alert", `Incident: ${ev.kind}: ${ev.detail}`, true);
        break;
    }
    if (FEED_TYPES.has(ev.type)) {
      this.feed.push(ev);
      if (this.feed.length > 400) this.feed.splice(0, this.feed.length - 400);
    }
    if (live) this.emit(ev);
  }
}

const FEED_TYPES = new Set(["task_created", "task_started", "task_done", "delegated", "chat",
  "captain_report", "task_paused", "task_error", "incident", "office_status", "meeting",
  "captain_message", "approval_requested", "approval_decided"]);

export function groupName(channel: string, state: OfficeState): string {
  return channel === "group:juno" ? `${state.name("chief_of_staff")} → All` : `${state.captain.nickname} → All`;
}

export function short(s: string, n = 140): string {
  s = s.replace(/\s+/g, " ");
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}
