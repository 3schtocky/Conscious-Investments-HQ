// HTML layer over the canvas: name tags, status icons, speech bubbles and room signs.
// DOM text stays crisp at any zoom, unlike canvas text in a pixel-art game.
import type { OfficeScene } from "../office/scene";
import type { OfficeState } from "../state";
import { h, plain } from "./dom";
import { bubbleTop, placeBubbles, tagLanes, type BubbleBox } from "./layout";

const LANE_PX = 19;   // how far a name tag steps up when it would overlap a neighbour's

interface Tag { root: HTMLElement; anchor: HTMLElement; name: HTMLElement; icon: HTMLElement; bubble: HTMLElement; bubbleUntil: number; lane: number }

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
    // Lay it out now, not on the next frame: speech is triggered by the game loop, which can run
    // after this frame's layout pass, and a new bubble must never flash outside the view.
    this.layout();
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
      t = { root, anchor, name, icon, bubble, bubbleUntil: 0, lane: 0 };
      this.tags.set(id, t);
    }
    return t;
  }

  /** Place every name tag and speech bubble. Colleagues standing side by side share the same few
   *  pixels, so tags step up into lanes and simultaneous bubbles spread apart (layout.ts); all
   *  measuring happens first and all writing after, so the browser lays out once. */
  private layout() {
    const scene = this.scene();
    if (!scene) return;
    const now = performance.now();
    const width = this.layer.clientWidth;
    const seen: { id: string; t: Tag; pos: { x: number; y: number; visible: boolean } }[] = [];
    for (const id of [...this.state.agents.keys(), "captain"]) {
      const pos = scene.headPosition(id);
      if (pos) seen.push({ id, t: this.tag(id), pos });
    }
    // measure
    const sized = seen.map((e) => ({ ...e, tagW: e.t.root.offsetWidth, bw: e.t.bubble.offsetWidth, bh: e.t.bubble.offsetHeight }));
    const lanes = tagLanes(sized.filter((e) => e.pos.visible).map((e) => ({ id: e.id, x: e.pos.x, w: e.tagW, lane: e.t.lane })));
    const talking = sized.filter((e) => e.pos.visible && e.t.bubbleUntil && now <= e.t.bubbleUntil);
    const boxes: BubbleBox[] = talking.map((e) => {
      const lane = (lanes.get(e.id) ?? 0) * LANE_PX;
      const below = e.pos.y - lane - e.bh - 24 < 4;   // no room above: drop below the speaker
      return { id: e.id, x: e.pos.x, y: e.pos.y, w: e.bw, h: e.bh, below, lift: below ? 0 : lane };
    });
    let placed = placeBubbles(boxes, 0, width);
    // A crowd can stack rows up past the top of the view: those speakers drop below instead.
    const clipped = boxes.filter((b) => !b.below && bubbleTop(b, placed.get(b.id)!) < 2);
    if (clipped.length) {
      for (const b of clipped) { b.below = true; b.lift = 0; }
      placed = placeBubbles(boxes, 0, width);
    }
    // write
    for (const e of sized) {
      const { t, pos } = e;
      t.root.style.transform = `translate(${pos.x}px, ${pos.y}px)`;
      t.root.style.display = pos.visible ? "" : "none";
      t.anchor.style.transform = t.root.style.transform;
      t.anchor.style.display = t.root.style.display;
      t.lane = lanes.get(e.id) ?? 0;
      t.root.style.setProperty("--lane", String(t.lane));
      const box = boxes.find((b) => b.id === e.id), at = placed.get(e.id);
      if (box && at) {
        const shift = at.cx - pos.x;
        const reach = Math.max(0, e.bw / 2 - 14);   // keep the tail on the bubble, pointing at the speaker
        t.bubble.style.setProperty("--shift", `${Math.round(shift)}px`);
        t.bubble.style.setProperty("--tail", `${Math.round(Math.max(-reach, Math.min(reach, -shift)))}px`);
        t.bubble.style.setProperty("--lift", `${Math.round(at.lift)}px`);
        t.bubble.classList.toggle("below", box.below);
      }
    }
  }

  private frame() {
    const scene = this.scene();
    if (scene) {
      this.layer.style.setProperty("--zoom", String(scene.zoom));
      const now = performance.now();
      for (const id of [...this.state.agents.keys(), "captain"]) {
        if (!scene.headPosition(id)) continue;
        const t = this.tag(id);
        t.name.textContent = this.state.name(id);
        const a = this.state.agents.get(id);
        // Drawn status indicators (no emojis on the floor): see .tag-icon[data-kind] in style.css
        const kind = !a ? "captain" : a.paused || a.status === "held" ? "paused" : this.state.clockedOut && a.status !== "working" ? "away"
          : a.live?.kind === "thinking" ? "thinking" : a.live?.kind === "text" ? "writing" : a.status === "working" ? "working" : "";
        if (t.icon.dataset.kind !== kind) {
          t.icon.dataset.kind = kind;
          t.icon.textContent = kind === "away" ? "zz" : "";
          t.icon.title = { captain: "The Captain", paused: "Paused", away: "Clocked out", thinking: "Thinking",
            writing: "Writing", working: "Working", "": "" }[kind] ?? "";
          if (a?.status === "held") t.icon.title = "On hold: the office is paused";
        }
        t.root.dataset.status = a ? (a.paused ? "paused" : a.status) : "captain";
        if (t.bubbleUntil && now > t.bubbleUntil) { t.bubble.classList.remove("show"); t.bubbleUntil = 0; }
        t.root.classList.toggle("speaking", t.bubbleUntil > 0);   // on a phone, bubbles are hidden: the tag shows who is talking
      }
      this.layout();   // after the text above is final, so tags are measured at their real width
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
