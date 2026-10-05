// The Micron demo on a phone: a player docked above the tab bar (act, caption, progress, controls),
// a camera that follows the action, a toast when the office finishes a file, and a one-time
// invitation. The recorded run itself is DemoTour (demoTour.ts); this only drives and shows it.
import type { OfficeState } from "../state";
import type { Captions } from "./captions";
import { actIndexAt, clock, followView, inviteDue, markerAt, shouldCut, skipTarget, UNLOCK_TEXT } from "./demoMobileModel";
import type { DemoTour } from "./demoTour";
import type { FileView } from "./demoReaders";
import { h } from "./dom";
import { tap } from "./haptics";

export interface MobileDemoDeps {
  tour: DemoTour;
  state: OfficeState;
  captions: Captions;
  view: { current: () => string; set: (id: string) => void };   // the map's camera
  goFloor: () => void;                                           // show the Floor tab, on the map
  openFile: (kind: FileView) => void;                            // the Demo files page
  onFloor: () => boolean;                                        // is the map on screen?
}

const INVITE_KEY = "hq-demo-invite";
/** The disclaimer under the player's controls, shortened to two lines. The full banner is on every file page. */
const PLAYER_NOTE = "Illustrative research, not investment advice. Real SEC data and a real model engine; scripted agents, no AI model called.";

export class MobileDemo {
  readonly player = h("div", { class: "m-player hidden", role: "region", "aria-label": "Demo player" });
  readonly toast = h("div", { class: "m-toast hidden", role: "status", "aria-live": "polite" });
  readonly invite = h("div", { class: "m-invite hidden", role: "note" });
  following = true;

  private seeking = false;
  private lastCut = -99;
  private inviteGone = false;     // dismissed (or taken up) this visit, whether or not storage works
  private opened = Date.now();
  private toastTimer = 0;
  private lastAct = -1;

  private step = h("span", { class: "mp-step" });
  private label = h("strong", { class: "mp-label" });
  private clockEl = h("span", { class: "mp-clock" });
  private fill = h("div", { class: "mp-fill" });
  private ticks = h("div", { class: "mp-ticks" });
  private bar = h("div", { class: "mp-bar", role: "progressbar", "aria-label": "Demo progress" }, this.fill, this.ticks);
  private prev = h("button", { class: "mp-btn", "aria-label": "Previous act", onclick: () => this.skip(-1) }, "⏮");
  private pauseIcon = h("span", { "aria-hidden": "true" });
  private pauseText = h("span", { class: "mp-pt" });   // the word is dropped on the narrowest phones, the icon stays
  private pause = h("button", { class: "mp-btn wide", onclick: () => { tap(); this.d.tour.togglePause(); } }, this.pauseIcon, this.pauseText);
  private next = h("button", { class: "mp-btn", "aria-label": "Next act", onclick: () => this.skip(1) }, "⏭");
  private follow = h("button", { class: "mp-chip", onclick: () => { tap(); this.following = !this.following; this.update(); } });
  private files = h("button", { class: "mp-chip", onclick: () => { tap(); this.d.openFile("home"); } });
  private note = h("div", { class: "mp-note" });
  private live = h("div", { class: "mp-live" });
  private end = h("div", { class: "mp-end hidden" });

  constructor(private d: MobileDemoDeps) {
    this.live.append(
      h("div", { class: "mp-top" }, this.step, this.label, this.clockEl,
        h("button", { class: "mp-x", "aria-label": "Close the demo", onclick: () => this.close() }, "✕")),
      this.bar,
      h("div", { class: "mp-ctl" }, this.prev, this.pause, this.next, this.follow, this.files),
      this.note);
    this.player.append(this.live, this.end);
    this.bar.addEventListener("pointerdown", (e) => this.tapBar(e));

    d.captions.rest = () => {
      const v = d.tour.view();
      return v && v.active && !v.finished ? v.acts[actIndexAt(v.acts, v.now)].caption : null;
    };
    d.tour.subscribe(() => this.update());
    d.tour.onUnlock = (artifact) => this.showToast(artifact);
    d.state.on((ev) => {   // the camera follows the action
      if (!ev || !d.state.touring || !this.following || this.seeking) return;
      const v = d.tour.view();
      if (!v || v.paused) return;
      const want = followView(ev, (id) => d.state.agents.get(id)?.wing);
      if (shouldCut({ want, current: d.view.current(), now: v.now, lastCut: this.lastCut })) { this.lastCut = v.now; d.view.set(want!); }
    });
    this.buildInvite();
    window.setInterval(() => this.maybeInvite(), 2000);
  }

  // ---- starting and stopping ------------------------------------------------------------------------
  start() {
    this.hideInvite(true);
    this.d.goFloor();
    this.following = true;
    this.lastCut = -99;
    this.d.captions.clear();
    this.d.view.set("floor");
    void this.d.tour.start();
  }

  close() {
    tap();
    this.d.tour.stop();
    this.d.captions.clear();
    this.d.view.set("floor");
  }

  /** The visitor moved the camera themselves (a chip, a swipe): stop steering it. */
  userMoved() {
    if (this.d.state.touring && this.following) { this.following = false; this.update(); }
  }

  // ---- skipping -------------------------------------------------------------------------------------
  private seekTo(t: number) {
    this.seeking = true;
    try { this.d.tour.seek(t); } finally { this.seeking = false; }
    this.d.captions.clear();
    this.lastCut = -99;
  }

  private skip(dir: -1 | 1) {
    const v = this.d.tour.view();
    if (!v) return;
    const to = skipTarget(v.acts, v.now, dir);
    if (to === null) return;
    tap();
    this.seekTo(to);
  }

  private tapBar(e: PointerEvent) {
    const v = this.d.tour.view();
    if (!v) return;
    const r = this.bar.getBoundingClientRect();
    const t = markerAt(v.acts, v.duration, (e.clientX - r.left) / r.width, r.width);
    if (t !== null) { tap(); this.seekTo(t); }
  }

  // ---- drawing ----------------------------------------------------------------------------------------
  /** Redrawn on every tick of the tour, so it only touches what changed. */
  update() {
    const v = this.d.tour.view();
    const on = !!v && v.active;
    this.player.classList.toggle("hidden", !on);
    if (!v || !on) return;
    const i = actIndexAt(v.acts, v.now);
    this.live.classList.toggle("hidden", v.finished);
    this.end.classList.toggle("hidden", !v.finished);
    if (v.finished) { this.drawEnd(v.unlocked.size); return; }
    const act = v.acts[i];
    const set = (el: HTMLElement, text: string) => { if (el.textContent !== text) el.textContent = text; };
    set(this.step, `${i + 1} of ${v.acts.length}`);
    set(this.label, act.label);
    set(this.clockEl, `${clock(v.now)} / ${clock(v.duration)}`);
    if (i !== this.lastAct) { this.lastAct = i; this.d.captions.render(); }   // the strip's resting line is the act's description
    this.fill.style.width = `${(v.now / v.duration) * 100}%`;
    if (!this.ticks.childElementCount) for (const a of v.acts.slice(1)) this.ticks.append(h("i", { style: `left:${(a.t / v.duration) * 100}%` }));
    set(this.pauseIcon, v.paused ? "▶" : "⏸");
    set(this.pauseText, v.paused ? " Resume" : " Pause");
    this.pause.setAttribute("aria-label", v.paused ? "Resume" : "Pause");
    this.prev.toggleAttribute("disabled", false);
    this.next.toggleAttribute("disabled", skipTarget(v.acts, v.now, 1) === null);
    set(this.follow, this.following ? "Following" : "Follow");
    this.follow.classList.toggle("on", this.following);
    const made = [...v.unlocked].filter((k) => k !== "position").length;
    set(this.files, `Files ${made}/3`);
    this.files.classList.toggle("on", made > 0);
    set(this.note, PLAYER_NOTE);
  }

  private drawEnd(files: number) {
    if (this.end.dataset.drawn === String(files)) return;
    this.end.dataset.drawn = String(files);
    this.end.replaceChildren(
      h("strong", {}, "That was the whole office, start to finish, in five minutes."),
      h("span", {}, "A recorded run of the real tools on real Micron filings. The report, model, note and position are in Demo files."),
      h("div", { class: "mp-end-actions" },
        h("button", { class: "m-btn primary", onclick: () => { tap(); this.start(); } }, "Watch again"),
        h("button", { class: "m-btn quiet", onclick: () => { tap(); this.d.openFile("home"); } }, "Open the files"),
        h("button", { class: "m-btn quiet", onclick: () => this.close() }, "Close the demo")),
      h("div", { class: "mp-note" }, this.d.tour.view()?.banner ?? ""));
  }

  // ---- the toast when a file is ready ------------------------------------------------------------------
  private showToast(artifact: string) {
    const text = UNLOCK_TEXT[artifact];
    if (!text) return;
    tap();
    window.clearTimeout(this.toastTimer);
    this.toast.replaceChildren(
      h("span", { class: "mt-text" }, text),
      h("button", { class: "mt-open", onclick: () => { this.hideToast(); this.d.openFile(artifact === "position" ? "position" : (artifact as FileView)); } }, "Open"));
    this.toast.classList.remove("hidden");
    requestAnimationFrame(() => this.toast.classList.add("show"));
    this.toastTimer = window.setTimeout(() => this.hideToast(), 6500);
  }

  private hideToast() {
    window.clearTimeout(this.toastTimer);
    this.toast.classList.remove("show");
    window.setTimeout(() => this.toast.classList.add("hidden"), 250);
  }

  // ---- the one-time invitation ---------------------------------------------------------------------------
  private buildInvite() {
    this.invite.append(
      h("span", { class: "m-hint-text" }, h("b", {}, "New here? "), "Watch the office build a Micron report in five minutes."),
      h("button", { class: "m-hint-go", onclick: () => this.start() }, "Start the tour"),
      h("button", { class: "m-hint-x", onclick: () => this.hideInvite(true) }, "Not now"));
  }

  private invited(): boolean {
    try { return localStorage.getItem(INVITE_KEY) === "1"; } catch { return this.inviteGone; }
  }

  /** The install hint waits until this is settled, so two cards never compete. */
  get inviteSettled(): boolean { return this.inviteGone || this.invited(); }

  private maybeInvite() {
    if (!this.invite.classList.contains("hidden") || this.inviteSettled) return;
    if (!inviteDue({ dismissed: false, touring: this.d.state.touring, secondsOpen: (Date.now() - this.opened) / 1000, onFloor: this.d.onFloor() })) return;
    this.invite.classList.remove("hidden");
  }

  private hideInvite(remember: boolean) {
    this.invite.classList.add("hidden");
    if (!remember) return;
    this.inviteGone = true;
    try { localStorage.setItem(INVITE_KEY, "1"); } catch { /* storage unavailable: it just won't return this visit */ }
  }
}
