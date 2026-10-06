// The phone's Feed: events turned into a work log. The office's events carry no task ids for a
// visitor, so an "assignment thread" is rebuilt from the sequence: an assignment starts under one
// colleague, what that colleague does next (hands work over, talks with someone) belongs to it, and
// it ends when they finish. Anything outside a thread is a short note. Pure, so it can be tested.
import type { OfficeEvent } from "../state";
import { captionFor, joinNames, words, type Names } from "./captionModel.ts";

export interface Step { ts: number; text: string; kind: "handoff" | "talk" | "returned" | "done" }
export interface Thread { kind: "thread"; id: string; agent: string; wing: string; title: string; startTs: number; endTs: number | null; steps: Step[] }
export interface Note { kind: "note"; ts: number; text: string; wing: string | null }
export type FeedItem = Thread | Note;

export interface FeedCtx extends Names { wingOf: (id: string) => string | undefined }

const MAX_STEPS = 8;

/** A line inside someone's own thread: the owner is already in the heading, so it is short and in
 *  the past tense ("Handed work to Ledger"). Words are quoted only where they are real (the demo). */
export function stepText(ev: OfficeEvent, ctx: Names): string | null {
  const said = words(ev.text);
  switch (ev.type) {
    case "chat": {
      if (String(ev.channel ?? "").startsWith("group:")) return said ? `Told everyone: “${said}”` : "Spoke to everyone";
      const to = joinNames(((ev.recipients as string[]) ?? []).map((r) => ctx.name(r)));
      if (!to) return null;
      return said ? `To ${to}: “${said}”` : `Talked with ${to}`;
    }
    case "delegated": { const job = words(ev.job, 90); return `Handed work to ${ctx.name(ev.to)}${job ? `: ${job}` : ""}`; }
    case "captain_report": return said ? `Reported to ${ctx.name("captain")}: “${said}”` : null;
    case "captain_message": return said ? `${ctx.name("captain")}: “${said}”` : null;
    case "approval_requested": { const t = words(ev.title, 90); return t ? `Asked ${ctx.name("captain")} to approve: ${t}` : null; }
    case "approval_decided": { const t = words(ev.title, 90); return t ? `${ctx.name("captain")} ${ev.decision === "approved" ? "approved" : "decided on"}: ${t}` : null; }
    default: return null;
  }
}
const lastTs = (i: FeedItem): number => (i.kind === "note" ? i.ts : Math.max(i.endTs ?? 0, i.startTs, ...i.steps.map((s) => s.ts)));

export function buildFeed(events: OfficeEvent[], ctx: FeedCtx, limit = 60): FeedItem[] {
  const ordered = [...events].sort((a, b) => a.ts - b.ts);
  const items: FeedItem[] = [];
  const open = new Map<string, Thread>();          // an unfinished thread, by who owns it
  const awaiting = new Map<string, Thread>();      // a colleague handed a job: whose thread it belongs to
  let n = 0;

  const note = (ev: OfficeEvent, text: string | null) => {
    if (!text) return;
    const prev = items[items.length - 1];
    if (prev?.kind === "note" && prev.text === text && ev.ts - prev.ts < 60) return;   // the same line twice in a minute is one line
    items.push({ kind: "note", ts: ev.ts, text, wing: ev.agent ? ctx.wingOf(ev.agent) ?? null : null });
  };
  const step = (t: Thread, ev: OfficeEvent, kind: Step["kind"], text: string | null) => {
    if (!text) return;
    const last = t.steps[t.steps.length - 1];
    if (last && last.text === text) { last.ts = ev.ts; return; }
    t.steps.push({ ts: ev.ts, text, kind });
    if (t.steps.length > MAX_STEPS) t.steps.splice(0, t.steps.length - MAX_STEPS);
  };

  for (const ev of ordered) {
    const agent = ev.agent ?? "";
    const mine = open.get(agent);
    switch (ev.type) {
      case "task_started": {
        if (ev.kind !== "assignment" || !ev.title || !agent) break;
        if (mine) mine.endTs = ev.ts;   // a new assignment replaces one left unfinished
        const t: Thread = { kind: "thread", id: `t${++n}`, agent, wing: ctx.wingOf(agent) ?? "", title: String(ev.title), startTs: ev.ts, endTs: null, steps: [] };
        open.set(agent, t);
        items.push(t);
        break;
      }
      case "task_done": {
        const owed = awaiting.get(agent);
        if (mine) { mine.endTs = ev.ts; step(mine, ev, "done", "Finished"); open.delete(agent); }
        else if (owed) { step(owed, ev, "returned", `${ctx.name(agent)} finished the job`); awaiting.delete(agent); }
        else note(ev, agent ? `${ctx.name(agent)} finished a task` : null);
        break;
      }
      case "delegated": {
        if (mine) { step(mine, ev, "handoff", stepText(ev, ctx)); if (ev.to) awaiting.set(String(ev.to), mine); }
        else note(ev, captionFor(ev, ctx));
        break;
      }
      case "chat": case "captain_report": case "approval_requested": case "approval_decided": case "captain_message": {
        if (mine) step(mine, ev, "talk", stepText(ev, ctx)); else note(ev, captionFor(ev, ctx));
        break;
      }
      case "meeting": note(ev, captionFor(ev, ctx)); break;
    }
  }
  return items.sort((a, b) => lastTs(b) - lastTs(a)).slice(0, limit);
}

export function itemWing(i: FeedItem): string | null { return i.kind === "note" ? i.wing : i.wing || null; }
export const itemTs = lastTs;

/** The wing filter: everything, or just the items of that wing (a note without a wing shows only under "All"). */
export function filterFeed(items: FeedItem[], wing: string | null): FeedItem[] {
  return wing ? items.filter((i) => itemWing(i) === wing) : items;
}

/** "now", "2m", "3h", "4d": short enough to sit beside a title. */
export function shortAgo(ts: number, nowMs = Date.now()): string {
  const s = Math.max(0, nowMs / 1000 - ts);
  if (s < 45) return "now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/** The heading a day's items sit under. */
export function dayLabel(ts: number, nowMs = Date.now()): string {
  const d = new Date(ts * 1000), now = new Date(nowMs);
  const day = (x: Date) => Date.UTC(x.getFullYear(), x.getMonth(), x.getDate());
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return d.toLocaleDateString("en-US", { weekday: "long" });
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export interface DayGroup { label: string; items: FeedItem[] }
export function groupByDay(items: FeedItem[], nowMs = Date.now()): DayGroup[] {
  const groups: DayGroup[] = [];
  for (const it of items) {
    const label = dayLabel(itemTs(it), nowMs);
    const g = groups[groups.length - 1];
    if (g && g.label === label) g.items.push(it); else groups.push({ label, items: [it] });
  }
  return groups;
}
