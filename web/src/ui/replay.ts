// The visitor's idle floor: when nothing real is happening, play back the most recent stretch of
// real work (movement and who works, never words) so the office never looks dead. Any live
// activity stops the replay at once and puts the floor back to what is true right now.
import type { OfficeScene } from "../office/scene";
import type { OfficeEvent, OfficeState } from "../state";
import { STATIC } from "../static";
import { h } from "./dom";

const IDLE_MS = STATIC ? 1_500 : 12_000;        // quiet this long before a replay starts
const LOOP_PAUSE_MS = STATIC ? 6_000 : 25_000;  // rest between replays
const RETRY_MS = 90_000;       // nothing to replay: look again later
const MIN_GAP = 450, MAX_GAP = 3200, SPEED_UP = 8;
// What counts as the office really being busy: anything that moves a colleague.
const ACTIVITY = new Set(["status", "move", "meeting", "chat", "delegated", "task_started"]);

interface ReplayData { events: OfficeEvent[]; from: number | null; to: number | null }

export class Replayer {
  readonly banner = h("div", { class: "replay-banner hidden", role: "status", "aria-live": "polite" });
  private data: ReplayData | null = null;
  private fetched = 0;
  private playing = false;
  private idx = 0;
  private timer: number | undefined;
  private quietSince = Date.now();
  private resumeAt = 0;   // do not start before this time

  constructor(private state: OfficeState, private scene: () => OfficeScene | null,
              private restore: () => Promise<void>) {
    window.setInterval(() => this.tick(), 2000);
  }

  /** A live event arrived over the socket. Real activity ends any replay. */
  live(ev: OfficeEvent) {
    if (!ACTIVITY.has(ev.type)) return;
    this.quietSince = Date.now();
    if (this.playing) this.stop();
  }

  private busy(): boolean {
    return [...this.state.agents.values()].some((a) => a.status === "working");
  }

  private tick() {
    if (this.playing || document.hidden) return;
    const now = Date.now();
    if (this.busy()) { this.quietSince = now; this.say(null); return; }
    if (now - this.quietSince < IDLE_MS || now < this.resumeAt) return;
    void this.start();
  }

  private async load(): Promise<ReplayData> {
    if (this.data && Date.now() - this.fetched < 10 * 60_000) return this.data;
    const res = await fetch("/api/public/replay").catch(() => null);
    this.data = res?.ok ? await res.json() : { events: [], from: null, to: null };
    this.fetched = Date.now();
    return this.data!;
  }

  private async start() {
    this.resumeAt = Date.now() + RETRY_MS;   // a failed or empty start waits before trying again
    const data = await this.load();
    if (this.playing || this.busy() || !this.scene() || !data.events.length) {
      if (!data.events.length) this.say("The office is between assignments. Recent work replays here when there is some.");
      return;
    }
    this.playing = true;
    this.state.replaying = true;
    this.idx = 0;
    const day = data.from ? new Date(data.from * 1000).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" }) : "";
    this.say(STATIC ? `Replay of the office at work${day ? `, ${day}` : ""}. Click a colleague or a wing to look around.`
      : `Replay of recent work${day ? `, ${day}` : ""}. The office is quiet right now; this goes live the moment it isn't.`);
    this.step();
  }

  private step() {
    const events = this.data!.events;
    if (this.idx >= events.length) { void this.finish(); return; }
    const ev = events[this.idx];
    this.state.apply({ ...ev });
    this.scene()?.handle(ev);
    const next = events[this.idx + 1];
    this.idx++;
    const gap = next ? Math.min(Math.max(((next.ts - ev.ts) * 1000) / SPEED_UP, MIN_GAP), MAX_GAP) : MIN_GAP;
    this.timer = window.setTimeout(() => this.step(), gap);
  }

  private async finish() {
    this.stop();
    this.resumeAt = Date.now() + LOOP_PAUSE_MS;
    this.data = null;   // fetch fresh next time: new work may have happened
  }

  private stop() {
    window.clearTimeout(this.timer);
    this.playing = false;
    this.state.replaying = false;
    this.say(null);
    void this.restore();   // the floor goes back to what is true right now
  }

  private say(text: string | null) {
    this.banner.textContent = text ?? "";
    this.banner.classList.toggle("hidden", !text);
  }
}
