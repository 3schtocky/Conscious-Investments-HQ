// Decisions behind the phone version of the Micron demo, free of the DOM so they can be tested.

export interface Act { t: number; label: string; caption: string }

/** Index of the act playing at time `t` (seconds). */
export function actIndexAt(acts: Act[], t: number): number {
  let i = 0;
  acts.forEach((a, k) => { if (a.t <= t) i = k; });
  return i;
}

/** Where "previous act" and "next act" land. Previous goes back to the start of the act you are in,
 *  unless you have only just begun it, and then to the one before; next is the following act. */
export function skipTarget(acts: Act[], t: number, dir: -1 | 1): number | null {
  const i = actIndexAt(acts, t);
  if (dir === 1) return acts[i + 1]?.t ?? null;
  if (t - acts[i].t > 3) return acts[i].t;
  return acts[i - 1]?.t ?? 0;
}

/** The act marker under a tap on the progress bar, if the tap is close enough to one. */
export function markerAt(acts: Act[], duration: number, fraction: number, widthPx: number, slopPx = 18): number | null {
  let best: { t: number; d: number } | null = null;
  for (const a of acts) {
    const d = Math.abs(a.t / duration - fraction) * widthPx;
    if (d <= slopPx && (!best || d < best.d)) best = { t: a.t, d };
  }
  return best ? best.t : null;
}

/** Which view the camera should be on for an event, or null to stay where it is. The floor is the
 *  cut for the executive suite and the Captain (Juno and Stott work across the whole office). */
const FOLLOW = new Set(["chat", "delegated", "task_started", "captain_report", "captain_message", "approval_requested", "approval_decided"]);
const WINGS = new Set(["equity_research", "screening", "audit", "client_relations", "quant"]);
export function followView(ev: { type: string; agent: string | null }, wingOf: (id: string) => string | undefined): string | null {
  if (ev.type === "meeting") return "lobby";
  if (!FOLLOW.has(ev.type)) return null;
  if (!ev.agent) return null;
  const wing = wingOf(ev.agent);
  if (!wing) return null;
  if (WINGS.has(wing)) return wing;
  return wing === "executive" ? "floor" : null;
}

/** Cut to the new view only if it is a change and the last cut was a moment ago: a camera that
 *  jumps on every line would make the visitor seasick. */
export function shouldCut(o: { want: string | null; current: string; now: number; lastCut: number; minGap?: number }): boolean {
  return !!o.want && o.want !== o.current && o.now - o.lastCut >= (o.minGap ?? 5);
}

export const UNLOCK_TEXT: Record<string, string> = {
  report: "The initiating-coverage report is ready",
  model: "The bull, base and bear model is ready",
  note: "The client note is ready",
  position: "The paper position is open",
};

/** The first-visit invitation: once, only when nothing else is going on, and not at the very start. */
export function inviteDue(o: { dismissed: boolean; touring: boolean; secondsOpen: number; onFloor: boolean }): boolean {
  return !o.dismissed && !o.touring && o.onFloor && o.secondsOpen >= 4;
}

export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
