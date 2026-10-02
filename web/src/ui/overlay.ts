// HTML layer over the canvas: name tags, status icons, speech bubbles and room signs.
// DOM text stays crisp at any zoom, unlike canvas text in a pixel-art game.
import type { OfficeScene } from "../office/scene";
import type { OfficeState } from "../state";
import { h, plain } from "./dom";

interface Tag { root: HTMLElement; anchor: HTMLElement; name: HTMLElement; icon: HTMLElement; bubble: HTMLElement; bubbleUntil: number }

export class Overlay {
  private tags = new Map<string, Tag>();
  private rooms = new Map<string, HTMLElement>();

  constructor(private layer: HTMLElement, private state: OfficeState, private scene: () => OfficeScene | null) {
    requestAnimationFrame(() => this.frame());
  }

  say(id: string, text: string, ms: number) {
    const t = this.tag(id);
    const clean = plain(text);
    t.bubble.textContent = clean.length > 220 ? clean.slice(0, 219) + "…" : clean;
    t.bubble.classList.add("show");
    t.bubbleUntil = performance.now() + ms;
    // Place it now, not on the next frame: speech is triggered by the game loop, which can run
    // after this frame's layout pass, and a new bubble must never flash outside the view.
    const pos = this.scene()?.headPosition(id);
    if (pos) { t.anchor.style.transform = `translate(${pos.x}px, ${pos.y}px)`; this.fit(t, pos); }
  }

  /** Keep a shown bubble inside the office view: it slides left near the right edge, right near
   *  the left edge (the tail keeps pointing at the speaker), and drops below near the top. */
  private fit(t: Tag, pos: { x: number; y: number }) {
    const w = t.bubble.offsetWidth, max = this.layer.clientWidth;
    const shift = Math.max(6 - (pos.x - w / 2), Math.min(0, max - 6 - (pos.x + w / 2)));
    t.bubble.style.setProperty("--shift", `${Math.round(shift)}px`);
    t.bubble.classList.toggle("below", pos.y - t.bubble.offsetHeight - 24 < 4);
  }

  private tag(id: string): Tag {
    let t = this.tags.get(id);
    if (!t) {
      const name = h("span", { class: "tag-name" });
      const icon = h("span", { class: "tag-icon" });
      const bubble = h("div", { class: "bubble" });
      const root = h("div", { class: "tag" }, h("div", { class: "tag-row" }, icon, name));
      // The bubble lives in its own layer above every name tag and sign, so nothing on the
      // floor can cover what a colleague is saying.
      const anchor = h("div", { class: "bubble-anchor" }, bubble);
      this.layer.append(root, anchor);
      t = { root, anchor, name, icon, bubble, bubbleUntil: 0 };
      this.tags.set(id, t);
    }
    return t;
  }

  private frame() {
    const scene = this.scene();
    if (scene) {
      const zoom = scene.zoom;
      this.layer.style.setProperty("--zoom", String(zoom));
      const ids = [...this.state.agents.keys(), "captain"];
      const now = performance.now();
      for (const id of ids) {
        const pos = scene.headPosition(id);
        if (!pos) continue;
        const t = this.tag(id);
        t.root.style.transform = `translate(${pos.x}px, ${pos.y}px)`;
        t.root.style.display = pos.visible ? "" : "none";
        t.anchor.style.transform = t.root.style.transform;
        t.anchor.style.display = t.root.style.display;
        t.name.textContent = this.state.name(id);
        const a = this.state.agents.get(id);
        // Drawn status indicators (no emojis on the floor): see .tag-icon[data-kind] in style.css
        const kind = !a ? "captain" : a.paused ? "paused" : this.state.clockedOut && a.status !== "working" ? "away"
          : a.live?.kind === "thinking" ? "thinking" : a.live?.kind === "text" ? "writing" : a.status === "working" ? "working" : "";
        if (t.icon.dataset.kind !== kind) {
          t.icon.dataset.kind = kind;
          t.icon.textContent = kind === "away" ? "zz" : "";
          t.icon.title = { captain: "The Captain", paused: "Paused", away: "Clocked out", thinking: "Thinking",
            writing: "Writing", working: "Working", "": "" }[kind] ?? "";
        }
        t.root.dataset.status = a ? (a.paused ? "paused" : a.status) : "captain";
        if (t.bubbleUntil && now > t.bubbleUntil) { t.bubble.classList.remove("show"); t.bubbleUntil = 0; }
        if (t.bubbleUntil) this.fit(t, pos);   // re-fit every frame as the speaker walks
      }
      for (const r of scene.roomLabelPositions()) {
        let el = this.rooms.get(r.id);
        if (!el) {
          el = h("div", { class: "room-sign" }, r.label);
          el.style.setProperty("--accent", r.accent);
          this.layer.append(el);
          this.rooms.set(r.id, el);
        }
        el.style.transform = `translate(${r.x}px, ${r.y}px)`;
      }
    }
    requestAnimationFrame(() => this.frame());
  }
}
