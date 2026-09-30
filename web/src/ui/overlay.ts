// HTML layer over the canvas: name tags, status icons, speech bubbles and room signs.
// DOM text stays crisp at any zoom, unlike canvas text in a pixel-art game.
import type { OfficeScene } from "../office/scene";
import type { OfficeState } from "../state";
import { h, plain } from "./dom";

interface Tag { root: HTMLElement; name: HTMLElement; icon: HTMLElement; bubble: HTMLElement; bubbleUntil: number }

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
  }

  private tag(id: string): Tag {
    let t = this.tags.get(id);
    if (!t) {
      const name = h("span", { class: "tag-name" });
      const icon = h("span", { class: "tag-icon" });
      const bubble = h("div", { class: "bubble" });
      const root = h("div", { class: "tag" }, bubble, h("div", { class: "tag-row" }, icon, name));
      this.layer.append(root);
      t = { root, name, icon, bubble, bubbleUntil: 0 };
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
        if (t.bubbleUntil) {   // keep the bubble inside the office view
          const w = t.bubble.offsetWidth, max = this.layer.clientWidth;
          const shift = Math.max(6 - (pos.x - w / 2), Math.min(0, max - 6 - (pos.x + w / 2)));
          t.bubble.style.setProperty("--shift", `${Math.round(shift)}px`);
          t.bubble.classList.toggle("below", pos.y - t.bubble.offsetHeight - 24 < 4);
        }
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
