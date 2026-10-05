// Tap a colleague on the phone's floor: a bottom sheet says who they are and what they are doing.
// Only what a visitor may see: role, persona, whether they are working, the current task, and the
// last few public actions (never their words). Drag the handle down, tap outside, or press close.
import { WING_ACCENT } from "../office/map";
import type { OfficeState } from "../state";
import { avatarImage, modelLabel, statusLabel } from "./panels";
import { captionFor, involves } from "./captionModel";
import { h, timeAgo } from "./dom";
import { tap } from "./haptics";

export class AgentSheet {
  readonly root = h("div", { class: "m-sheet-root hidden" });
  private panel = h("div", { class: "m-sheet", role: "dialog", "aria-modal": "true", "aria-label": "Colleague" });
  private id: string | null = null;
  private timer = 0;
  private closing = 0;

  constructor(private state: OfficeState) {
    this.root.append(h("div", { class: "m-sheet-back", onclick: () => this.close() }), this.panel);
    state.on(() => { if (this.id && !this.timer) this.timer = window.setTimeout(() => { this.timer = 0; this.render(); }, 400); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && this.id) this.close(); });
  }

  get isOpen(): boolean { return this.id !== null; }

  open(id: string) {
    if (!this.state.agents.has(id)) return;
    window.clearTimeout(this.closing);
    this.id = id;
    this.render();
    this.panel.style.transform = "";
    this.root.classList.remove("hidden");
    requestAnimationFrame(() => this.root.classList.add("open"));   // next frame, so the slide-up animates
    tap();
  }

  close() {
    if (!this.id) return;
    this.id = null;
    this.root.classList.remove("open");
    this.panel.style.transform = "";
    this.closing = window.setTimeout(() => this.root.classList.add("hidden"), 260);
  }

  private render() {
    const a = this.id ? this.state.agents.get(this.id) : undefined;
    if (!a) { this.close(); return; }
    const recent = this.state.feed.filter((ev) => involves(ev, a.id)).reverse()
      .map((ev) => ({ ev, text: captionFor(ev, this.state) })).filter((r): r is { ev: typeof r.ev; text: string } => !!r.text).slice(0, 4);
    this.panel.style.setProperty("--accent", WING_ACCENT[a.wing] ?? "#46505e");
    this.panel.replaceChildren(
      this.grab(),
      h("div", { class: "m-sheet-head" },
        avatarImage(a.id, a.avatar, 4),
        h("div", { class: "m-sheet-id" },
          h("h2", {}, a.nickname),
          h("div", { class: "m-sheet-role" }, `${a.role} · ${this.state.wings[a.wing] ?? a.wing}`),
          h("div", { class: "badges" },
            h("span", { class: `pill m-status-${a.status}` }, statusLabel(a, this.state.clockedOut)),
            h("span", { class: "pill model" }, modelLabel(a.model_id))))),
      h("p", { class: "m-sheet-persona" }, a.persona),
      h("div", { class: "m-sheet-label" }, "Right now"),
      h("div", { class: "m-sheet-now" }, a.task ? a.task.title : "No assignment at the moment."),
      h("div", { class: "m-sheet-label" }, "Recently"),
      recent.length
        ? h("ul", { class: "m-sheet-recent" }, ...recent.map((r) => h("li", {}, h("span", {}, r.text), h("time", {}, timeAgo(r.ev.ts)))))
        : h("p", { class: "m-sheet-none" }, "Nothing to show yet. Their words stay inside the office; finished work appears in the Feed."),
      h("button", { class: "m-sheet-close", onclick: () => this.close() }, "Close"));
  }

  /** The handle: drag it down to dismiss. */
  private grab(): HTMLElement {
    const grab = h("div", { class: "m-sheet-grab", "aria-hidden": "true" }, h("span", {}));
    let y0 = 0, t0 = 0, dy = 0, live = false;
    grab.addEventListener("pointerdown", (e) => { live = true; y0 = e.clientY; t0 = e.timeStamp; dy = 0; grab.setPointerCapture(e.pointerId); this.panel.style.transition = "none"; });
    grab.addEventListener("pointermove", (e) => { if (!live) return; dy = Math.max(0, e.clientY - y0); this.panel.style.transform = `translateY(${dy}px)`; });
    const end = (e: PointerEvent) => {
      if (!live) return;
      live = false;
      this.panel.style.transition = "";
      const speed = dy / Math.max(1, e.timeStamp - t0);   // px per ms
      if (dy > 90 || speed > 0.6) this.close(); else this.panel.style.transform = "";
    };
    grab.addEventListener("pointerup", end);
    grab.addEventListener("pointercancel", end);
    return grab;
  }
}
