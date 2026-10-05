// Plain-language lines for what is happening on the floor, built only from events a visitor is
// allowed to receive. On the public site speech arrives as "…" (the words stay private), so a chat
// becomes "Juno is talking with Scout". In the recorded Demo the words are real and scripted for
// visitors, and then the line quotes them. No DOM here, so it can be tested.
import type { OfficeEvent } from "../state";

export interface Names { name: (id: string | null | undefined) => string }

/** "A", "A and B", "A, B and C". */
export function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

const tidy = (s: string, max: number) => {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
};
/** Real words, or null when they are hidden ("…" or nothing). */
const words = (text: unknown, max = 120): string | null => (!text || text === "…" ? null : tidy(String(text), max));

export function captionFor(ev: OfficeEvent, who: Names): string | null {
  const a = who.name(ev.agent);
  switch (ev.type) {
    case "chat": {
      const said = words(ev.text);
      if (String(ev.channel ?? "").startsWith("group:")) return said ? `${a} to everyone: “${said}”` : `${a} is speaking to everyone`;
      const to = joinNames(((ev.recipients as string[]) ?? []).map((r) => who.name(r)));
      if (!to) return null;
      return said ? `${a} to ${to}: “${said}”` : `${a} is talking with ${to}`;
    }
    case "captain_message": {
      const said = words(ev.text);
      return said ? `${who.name("captain")}: “${said}”` : null;
    }
    case "delegated": {
      const job = words(ev.job, 90);
      return `${a} handed work to ${who.name(ev.to)}${job ? `: ${job}` : ""}`;
    }
    case "meeting": {
      const names = ((ev.participants as string[]) ?? []).map((p) => who.name(p));
      return names.length > 1 ? `${joinNames(names)} gathered in the Lobby` : null;
    }
    case "captain_report": {   // only in the Demo: on the public site these never arrive
      const said = words(ev.text, 110);
      return said ? `${a} to ${who.name("captain")}: “${said}”` : null;
    }
    case "approval_requested": {
      const t = words(ev.title, 90);
      return t ? `${a} asked ${who.name("captain")} to approve: ${t}` : null;
    }
    case "approval_decided": {
      const t = words(ev.title, 90);
      return t ? `${who.name("captain")} ${ev.decision === "approved" ? "approved" : "decided on"}: ${t}` : null;
    }
    case "task_started":   // sub-jobs are noise; an assignment is the news
      return ev.kind === "assignment" && ev.title ? `${a} started “${tidy(String(ev.title), 80)}”` : null;
    case "task_done":
      return ev.agent ? `${a} finished a task` : null;
    default:
      return null;
  }
}

/** Does this event involve the agent (they did it, or it was done to or with them)? */
export function involves(ev: OfficeEvent, id: string): boolean {
  return ev.agent === id || ev.to === id
    || (Array.isArray(ev.recipients) && ev.recipients.includes(id))
    || (Array.isArray(ev.participants) && ev.participants.includes(id));
}

export interface Line { text: string; ts: number }

/** The newest few lines that are still fresh, oldest first. Identical neighbours collapse. */
export function pushLine(lines: Line[], line: Line, keep = 3): Line[] {
  const last = lines[lines.length - 1];
  const next = last && last.text === line.text ? [...lines.slice(0, -1), line] : [...lines, line];
  return next.slice(-keep);
}
export function freshLines(lines: Line[], now: number, maxAgeMs = 25_000): Line[] {
  return lines.filter((l) => now - l.ts <= maxAgeMs);
}

/** What the strip says when no line is fresh. */
export function idleLine(o: { workingNames: string[]; replaying: boolean; held: boolean; clockedOut: boolean }): string {
  if (o.replaying) return "Replaying recent work";
  if (o.held) return "The office is paused";
  if (o.clockedOut) return "The office has clocked out for the day";
  if (o.workingNames.length) return `${joinNames(o.workingNames)} ${o.workingNames.length === 1 ? "is" : "are"} working`;
  return "Quiet right now";
}
