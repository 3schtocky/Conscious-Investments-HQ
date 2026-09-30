// The office floor: pixel-art rooms, desks and props, plus the walking cast.
import Phaser from "phaser";
import { drawSheet, ID_PRESET, FRAME_H, FRAME_W, imageOf, resolveParts, type AvatarSpec, type Dir } from "./avatar";
import {
  buildDesks, COLS, DOORS, PROPS, ROOMS, ROWS, SEATS, TABLE, TILE, walkable,
  type Desk, type Pt, type Rect,
} from "./map";
import { findPath } from "./path";
import type { OfficeEvent, OfficeState } from "../state";


const WALK_SPEED = 64;          // px per second
const LOBBY_LINGER_MS = 9000;   // gathered colleagues drift back to their desks after this

export type Focus = { kind: "floor" } | { kind: "wing"; id: string } | { kind: "agent"; id: string };

export interface SceneHooks {
  onAgentClick: (id: string) => void;
  onRoomClick: (roomId: string) => void;
  say: (id: string, text: string, ms: number) => void;
}

type Action = { type: "walk"; to: Pt } | { type: "say"; text: string } | { type: "wait"; ms: number };

class Walker {
  tile: Pt;
  path: Pt[] = [];
  queue: Action[] = [];
  dir: Dir = "down";
  flip = false;
  frameClock = 0;
  idleSince = 0;
  seat: number | null = null;
  waitUntil = 0;
  image = false;
  specKey = "";
  constructor(public id: string, public sprite: Phaser.GameObjects.Sprite, public desk: Desk) {
    this.tile = { ...desk.chair };
  }
  get home(): Pt { return this.desk.chair; }
  atHome(): boolean { return this.tile.x === this.home.x && this.tile.y === this.home.y; }
}

export class OfficeScene extends Phaser.Scene {
  private state!: OfficeState;
  private hooks!: SceneHooks;
  private desks: Desk[] = [];
  private grid: boolean[][] = [];
  private walkers = new Map<string, Walker>();
  private glows = new Map<string, Phaser.GameObjects.Rectangle>();
  private seatsTaken = new Set<number>();
  private focus: Focus = { kind: "floor" };
  private ready = false;

  constructor() { super("office"); }

  init(data: { state: OfficeState; hooks: SceneHooks; focus?: Focus }) {
    this.state = data.state;
    this.hooks = data.hooks;
    if (data.focus) this.focus = data.focus;
  }

  create() {
    this.cameras.main.setBackgroundColor("#d8d3c7");
    this.cameras.main.setBounds(-TILE, -TILE, (COLS + 2) * TILE, (ROWS + 2) * TILE);
    this.drawFloors();
    this.drawWalls();
    this.drawProps();
    this.input.on("pointerdown", (p: Phaser.Input.Pointer, over: Phaser.GameObjects.GameObject[]) => {
      if (over.length) return;
      const t = { x: Math.floor(p.worldX / TILE), y: Math.floor(p.worldY / TILE) };
      const room = ROOMS.find((r) => t.x >= r.rect.x && t.x < r.rect.x + r.rect.w && t.y >= r.rect.y && t.y < r.rect.y + r.rect.h);
      if (room) this.hooks.onRoomClick(room.id);
    });
    this.scale.on("resize", (size: Phaser.Structs.Size) => {
      this.cameras.resize(size.width, size.height);
      this.applyFocus(false);
    });
    this.ready = true;
    this.sync();
    this.applyFocus(false);
  }

  // ---- cast -------------------------------------------------------------------------------
  /** Create or refresh walkers and desks from the current state (roster changes included). */
  sync() {
    if (!this.ready) return;
    const agents = [...this.state.agents.values()];
    if (!this.desks.length) {
      this.desks = buildDesks(agents);
      this.grid = walkable(this.desks);
      for (const d of this.desks) this.drawDesk(d);
    }
    const members: { id: string; avatar: AvatarSpec }[] = [
      ...agents.map((a) => ({ id: a.id, avatar: a.avatar })),
      { id: "captain", avatar: this.state.captain.avatar ?? "captain" },
    ];
    for (const m of members) {
      const desk = this.desks.find((d) => d.owner === m.id);
      if (!desk) continue;
      let w = this.walkers.get(m.id);
      const specKey = JSON.stringify(m.avatar ?? null);
      if (!w) {
        const sprite = this.add.sprite(0, 0, "__DEFAULT").setOrigin(0.5, 1).setInteractive({ useHandCursor: true });
        sprite.on("pointerdown", () => this.hooks.onAgentClick(m.id));
        w = new Walker(m.id, sprite, desk);
        this.walkers.set(m.id, w);
        this.place(w);
      }
      if (w.specKey !== specKey) {
        w.specKey = specKey;
        this.applyAvatar(w, m.avatar);
      }
    }
    this.refreshGlows();
  }

  private applyAvatar(w: Walker, spec: AvatarSpec) {
    const img = imageOf(spec);
    const key = `av-${w.id}-${Date.now()}`;
    if (img) {
      const el = new Image();
      el.onload = () => {
        this.textures.addImage(key, el);
        w.image = true;
        w.sprite.setTexture(key);
        const s = Math.min(FRAME_W / el.width, FRAME_H / el.height);
        w.sprite.setScale(s);
      };
      el.src = img;
      return;
    }
    const sheet = drawSheet(resolveParts(spec, w.id === "captain" ? "captain" : this.presetKey(w.id)));
    const tex = this.textures.addCanvas(key, sheet)!;
    (["down", "up", "side"] as Dir[]).forEach((d, row) => {
      for (let f = 0; f < 3; f++) tex.add(`${d}${f}`, 0, f * FRAME_W, row * FRAME_H, FRAME_W, FRAME_H);
    });
    w.image = false;
    w.sprite.setScale(1);
    w.sprite.setTexture(key, `${w.dir}0`);
  }

  private presetKey(id: string): string {
    return ID_PRESET[id] ?? id;
  }

  private place(w: Walker) {
    w.sprite.setPosition(w.tile.x * TILE + TILE / 2, (w.tile.y + 1) * TILE);
    w.sprite.setDepth((w.tile.y + 1) * TILE);
  }

  // ---- events -----------------------------------------------------------------------------
  handle(ev: OfficeEvent) {
    const w = ev.agent ? this.walkers.get(ev.agent) : undefined;
    switch (ev.type) {
      case "move": {
        if (!w) return;
        const target = this.resolveTarget(ev.to, w);
        if (target) w.queue.push({ type: "walk", to: target });
        break;
      }
      case "meeting":
        for (const id of ev.participants as string[]) {
          const p = this.walkers.get(id);
          if (!p) continue;
          const seat = this.takeSeat(p);
          if (seat) p.queue.push({ type: "walk", to: seat });
        }
        break;
      case "chat":
        if (w) w.queue.push({ type: "say", text: ev.text });
        break;
      case "captain_report":
        if (w) w.queue.push({ type: "say", text: `To ${this.state.captain.nickname}: ${ev.text}` });
        break;
      case "delegated":
        if (w) w.queue.push({ type: "say", text: `Over to you, ${this.state.name(ev.to)}.` });
        break;
      case "task_started": {
        // A new task pulls an agent back to their desk, unless they're mid-errand.
        if (w && !w.atHome() && !w.queue.length && !w.path.length && w.seat === null) {
          w.queue.push({ type: "walk", to: w.home });
        }
        break;
      }
      case "status":
      case "roster_updated":
        this.refreshGlows();
        break;
    }
  }

  private resolveTarget(to: string, w: Walker): Pt | null {
    if (to.startsWith("desk:")) {
      const owner = to.slice(5);
      if (owner === w.id) { this.releaseSeat(w); return w.home; }
      const d = this.desks.find((x) => x.owner === owner);
      return d ? d.visitor : null;
    }
    if (to === "lobby") return this.takeSeat(w);
    return null;
  }

  private takeSeat(w: Walker): Pt | null {
    if (w.seat !== null) return SEATS[w.seat];
    const i = SEATS.findIndex((_, k) => !this.seatsTaken.has(k));
    if (i < 0) return null;
    this.seatsTaken.add(i);
    w.seat = i;
    return SEATS[i];
  }

  private releaseSeat(w: Walker) {
    if (w.seat !== null) this.seatsTaken.delete(w.seat);
    w.seat = null;
  }

  // ---- per-frame --------------------------------------------------------------------------
  update(time: number, delta: number) {
    for (const w of this.walkers.values()) this.step(w, time, delta);
  }

  private step(w: Walker, time: number, delta: number) {
    if (w.path.length) {
      const next = w.path[0];
      const tx = next.x * TILE + TILE / 2, ty = (next.y + 1) * TILE;
      const dx = tx - w.sprite.x, dy = ty - w.sprite.y;
      const dist = Math.hypot(dx, dy);
      const move = (WALK_SPEED * delta) / 1000;
      if (Math.abs(dx) > Math.abs(dy)) { w.dir = "side"; w.flip = dx < 0; } else { w.dir = dy < 0 ? "up" : "down"; w.flip = false; }
      if (dist <= move) {
        w.sprite.setPosition(tx, ty);
        w.tile = next;
        w.path.shift();
      } else {
        w.sprite.x += (dx / dist) * move;
        w.sprite.y += (dy / dist) * move;
      }
      w.sprite.setDepth(w.sprite.y);
      w.frameClock += delta;
      this.setFrame(w, 1 + (Math.floor(w.frameClock / 140) % 2));
      if (!w.path.length) {
        // Arrived: face the room (down) at a desk or seat.
        w.dir = "down"; w.flip = false; this.setFrame(w, 0);
        w.idleSince = time;
      }
      return;
    }
    if (w.waitUntil > time) return;
    const action = w.queue.shift();
    if (action) {
      w.idleSince = time;
      if (action.type === "walk") {
        const path = findPath(this.grid, w.tile, action.to);
        if (path.length) { w.path = path; if (w.seat !== null && !SEATS.some((s) => s.x === action.to.x && s.y === action.to.y)) this.releaseSeat(w); }
      } else if (action.type === "say") {
        const ms = Math.min(9000, 2500 + action.text.length * 45);
        this.hooks.say(w.id, action.text, ms);
        w.waitUntil = time + Math.min(ms, 3500);
      } else {
        w.waitUntil = time + action.ms;
      }
      return;
    }
    // Idle away from the desk (e.g. after a Lobby meeting): drift home after a while.
    if (!w.atHome() && w.id !== "captain" && time - w.idleSince > LOBBY_LINGER_MS) {
      this.releaseSeat(w);
      w.queue.push({ type: "walk", to: w.home });
    }
  }

  private setFrame(w: Walker, f: number) {
    if (w.image) {
      w.sprite.setFlipX(w.flip);
      w.sprite.setAngle(f === 0 ? 0 : f === 1 ? -4 : 4);
      return;
    }
    w.sprite.setFrame(`${w.dir}${f}`);
    w.sprite.setFlipX(w.flip);
  }

  /** Screen position (CSS px, relative to the canvas) of the top of an agent's head. */
  headPosition(id: string): { x: number; y: number; visible: boolean } | null {
    const w = this.walkers.get(id);
    if (!w) return null;
    const cam = this.cameras.main;
    const x = (w.sprite.x - cam.worldView.x) * cam.zoom;
    const y = (w.sprite.y - FRAME_H - cam.worldView.y) * cam.zoom;
    const visible = x > -40 && y > -40 && x < cam.width + 40 && y < cam.height + 40;
    return { x, y, visible };
  }

  roomLabelPositions(): { id: string; label: string; x: number; y: number; accent: string }[] {
    const cam = this.cameras.main;
    return ROOMS.map((r) => ({
      id: r.id, label: r.label, accent: r.accent,
      x: ((r.rect.x + r.rect.w / 2) * TILE - cam.worldView.x) * cam.zoom,
      y: ((r.rect.y + (r.id === "lobby" ? 0.6 : r.rect.h - 0.2)) * TILE - cam.worldView.y) * cam.zoom,
    }));
  }

  get zoom(): number { return this.cameras.main.zoom; }

  // ---- camera -----------------------------------------------------------------------------
  setFocus(f: Focus) {
    this.focus = f;
    this.applyFocus(true);
  }

  private applyFocus(animate: boolean) {
    if (!this.ready) return;
    const cam = this.cameras.main;
    cam.stopFollow();
    let rect: Rect = { x: 0, y: 0, w: COLS, h: ROWS };
    if (this.focus.kind === "wing") {
      const r = ROOMS.find((x) => x.id === (this.focus as any).id);
      if (r) rect = { x: r.rect.x - 1, y: r.rect.y - 1, w: r.rect.w + 2, h: r.rect.h + 2 };
    }
    if (this.focus.kind === "agent") {
      const w = this.walkers.get(this.focus.id);
      if (w) {
        const zoom = this.fitZoom({ x: 0, y: 0, w: 14, h: 9 });
        if (animate) cam.zoomTo(zoom, 350); else cam.setZoom(zoom);
        cam.startFollow(w.sprite, true, 0.12, 0.12, 0, FRAME_H / 2);
        return;
      }
    }
    const zoom = this.fitZoom(rect);
    const cx = (rect.x + rect.w / 2) * TILE, cy = (rect.y + rect.h / 2) * TILE;
    if (animate) { cam.zoomTo(zoom, 350); cam.pan(cx, cy, 350, "Sine.easeInOut"); }
    else { cam.setZoom(zoom); cam.centerOn(cx, cy); }
  }

  private fitZoom(rect: Rect): number {
    const cam = this.cameras.main;
    return Math.min(cam.width / (rect.w * TILE), cam.height / (rect.h * TILE));
  }

  // ---- art --------------------------------------------------------------------------------
  private refreshGlows() {
    for (const d of this.desks) {
      const g = this.glows.get(d.owner);
      if (!g) continue;
      const a = this.state.agents.get(d.owner);
      const status = d.owner === "captain" ? "idle" : a?.status ?? "idle";
      const color = a?.paused ? 0xe0a030 : status === "working" ? 0x7fd6ff : 0x3a3f4a;
      g.setFillStyle(color, status === "working" || a?.paused ? 1 : 0.9);
    }
  }

  private drawFloors() {
    const g = this.add.graphics().setDepth(0);
    for (const r of ROOMS) {
      const [a, b] = r.floor;
      for (let y = r.rect.y; y < r.rect.y + r.rect.h; y++) {
        for (let x = r.rect.x; x < r.rect.x + r.rect.w; x++) {
          const alt = r.id === "lobby" ? y % 2 === 0 : (x + y) % 2 === 0;
          g.fillStyle(Phaser.Display.Color.HexStringToColor(alt ? a : b).color);
          g.fillRect(x * TILE, y * TILE, TILE, TILE);
          if (r.id === "lobby" || r.id === "executive") {   // plank seams
            g.fillStyle(Phaser.Display.Color.HexStringToColor(b).darken(6).color);
            g.fillRect(x * TILE, y * TILE + TILE - 1, TILE, 1);
            if ((x + (y % 3)) % 3 === 0) g.fillRect(x * TILE, y * TILE, 1, TILE);
          }
        }
      }
    }
    for (const d of DOORS) {   // door mats
      g.fillStyle(0xb9b5ad).fillRect(d.x * TILE, d.y * TILE + 3, TILE, TILE - 6);
    }
  }

  private drawWalls() {
    const g = this.add.graphics().setDepth(1);
    const isDoor = (x: number, y: number) => DOORS.some((d) => d.x === x && d.y === y);
    const wallTile = (x: number, y: number) => {
      if (isDoor(x, y)) return;
      g.fillStyle(0x6f675b).fillRect(x * TILE, y * TILE, TILE, TILE / 2);
      g.fillStyle(0xa79d8a).fillRect(x * TILE, y * TILE + TILE / 2, TILE, TILE / 2);
      g.fillStyle(0x8a8171).fillRect(x * TILE, y * TILE + TILE - 2, TILE, 2);
    };
    for (let x = 0; x < COLS; x++) { wallTile(x, 0); wallTile(x, 11); wallTile(x, 21); wallTile(x, ROWS - 1); }
    for (let y = 0; y < ROWS; y++) {
      const cols = y < 11 ? [0, 12, 24, 36, 48] : y > 21 ? [0, 16, 32, 48] : [0, 48];
      for (const x of cols) {
        g.fillStyle(0x6f675b).fillRect(x * TILE, y * TILE, TILE, TILE);
        g.fillStyle(0x7d7466).fillRect(x * TILE + 3, y * TILE, TILE - 6, TILE);
      }
    }
    // wing accent stripes above each door
    for (const r of ROOMS) {
      if (["lobby"].includes(r.id)) continue;
      const c = Phaser.Display.Color.HexStringToColor(r.accent).color;
      const y = r.rect.y < 11 ? 11 : 21;
      g.fillStyle(c).fillRect(r.rect.x * TILE, y * TILE + TILE / 2 - 2, r.rect.w * TILE, 2);
    }
  }

  private drawDesk(d: Desk) {
    const x = (d.chair.x - 1) * TILE, y = (d.chair.y + 1) * TILE;
    const captain = d.owner === "captain";
    // chair back (behind the sitter)
    const accent = this.accentFor(d);
    const chair = this.add.graphics().setDepth((d.chair.y + 1) * TILE - 1);
    chair.fillStyle(0x2E2C29).fillRect(d.chair.x * TILE + 1, d.chair.y * TILE - 3, TILE - 2, 13);
    chair.fillStyle(accent).fillRect(d.chair.x * TILE + 2, d.chair.y * TILE - 2, TILE - 4, 11);
    // desk (in front of the sitter)
    const g = this.add.graphics().setDepth((d.chair.y + 2) * TILE);
    const top = captain ? 0x6b4a2e : 0xb98a5a, front = captain ? 0x563a23 : 0x9a6f45;
    g.fillStyle(0x2E2C29).fillRect(x - 1, y - 7, TILE * 3 + 2, TILE + 8);
    g.fillStyle(top).fillRect(x, y - 6, TILE * 3, 7);
    g.fillStyle(front).fillRect(x, y + 1, TILE * 3, TILE - 3);
    g.fillStyle(0x2E2C29).fillRect(x + 3, y + TILE - 2, 2, 2).fillRect(x + TILE * 3 - 5, y + TILE - 2, 2, 2);
    if (captain) g.fillStyle(0xc9a227).fillRect(x, y + 1, TILE * 3, 1).fillRect(x + TILE + 2, y + 6, TILE - 4, 3);
    // monitor (seen from behind) with a status glow along its top
    const mx = x + TILE * 2 + 1;
    g.fillStyle(0x2E2C29).fillRect(mx + 6, y - 8, 2, 4);
    g.fillStyle(0x2E2C29).fillRect(mx - 1, y - 17, TILE, 10);
    g.fillStyle(0x3a3f4a).fillRect(mx, y - 16, TILE - 2, 8);
    const glow = this.add.rectangle(mx + (TILE - 2) / 2, y - 16, TILE - 2, 2, 0x3a3f4a).setOrigin(0.5, 0)
      .setDepth((d.chair.y + 2) * TILE + 1);
    this.glows.set(d.owner, glow);
    // a mug and papers for character
    g.fillStyle(0xf6f5f0).fillRect(x + 3, y - 5, 6, 4);
    g.fillStyle(accent).fillRect(x + TILE - 4, y - 4, 4, 3);
  }

  private accentFor(d: Desk): number {
    const room = ROOMS.find((r) => r.id === d.room);
    return Phaser.Display.Color.HexStringToColor(room?.accent ?? "#46505e").color;
  }

  private drawProps() {
    for (const p of PROPS) {
      const { x, y, w, h } = p.rect;
      const px = x * TILE, py = y * TILE;
      const g = this.add.graphics().setDepth(p.kind === "rug" ? 0.5 : (y + h) * TILE);
      switch (p.kind) {
        case "plant":
          g.fillStyle(0x8a5a33).fillRect(px + 4, py + 9, 8, 7);
          g.fillStyle(0x3a9a7a).fillCircle(px + 8, py + 5, 6);
          g.fillStyle(0x2f7d63).fillCircle(px + 5, py + 7, 3).fillCircle(px + 11, py + 4, 3);
          break;
        case "couch":
          g.fillStyle(0x2E2C29).fillRect(px - 1, py - 1, w * TILE + 2, h * TILE + 2);
          g.fillStyle(0x46505e).fillRect(px, py, w * TILE, h * TILE);
          g.fillStyle(0x5b6677).fillRect(px + 3, py + 8, w * TILE - 6, h * TILE - 11);
          for (let i = 1; i < w; i++) g.fillStyle(0x46505e).fillRect(px + i * TILE, py + 8, 1, h * TILE - 11);
          break;
        case "cooler":
          g.fillStyle(0xe8eef2).fillRect(px + 3, py + 4, 10, 12);
          g.fillStyle(0x7fa8d0).fillRect(px + 4, py - 4, 8, 9);
          g.fillStyle(0x2a68a8).fillRect(px + 6, py + 9, 4, 2);
          break;
        case "reception":
          g.fillStyle(0x2E2C29).fillRect(px - 1, py - 5, w * TILE + 2, TILE + 6);
          g.fillStyle(0xb98a5a).fillRect(px, py - 4, w * TILE, 6);
          g.fillStyle(0x9a6f45).fillRect(px, py + 2, w * TILE, TILE - 3);
          g.fillStyle(0xc9a227).fillRect(px + w * TILE / 2 - 10, py + 6, 20, 3);
          break;
        case "bookshelf":
          g.fillStyle(0x6b4a2e).fillRect(px, py - 10, w * TILE, TILE + 10);
          for (let s = 0; s < 3; s++) {
            for (let b = 0; b < w * 4; b++) {
              const colors = [0x2a68a8, 0xd27030, 0x3a9a7a, 0x8a6db8, 0xc9a227, 0xf6f5f0];
              g.fillStyle(colors[(b * 7 + s * 3 + x) % colors.length]).fillRect(px + 2 + b * 3.5, py - 8 + s * 8, 3, 6);
            }
          }
          break;
        case "whiteboard":
          g.fillStyle(0x9c9c9c).fillRect(px, py - 12, w * TILE, TILE + 4);
          g.fillStyle(0xfdfdfb).fillRect(px + 2, py - 10, w * TILE - 4, TILE);
          g.lineStyle(1, 0xd27030).beginPath().moveTo(px + 6, py + 2).lineTo(px + 20, py - 4).lineTo(px + 34, py - 1).lineTo(px + 50, py - 8).strokePath();
          g.lineStyle(1, 0x2a68a8).beginPath().moveTo(px + 6, py + 3).lineTo(px + 24, py).lineTo(px + 44, py + 1).lineTo(px + 70, py - 6).strokePath();
          break;
        case "cabinet":
          g.fillStyle(0x2E2C29).fillRect(px - 1, py - 9, w * TILE + 2, TILE + 10);
          g.fillStyle(0x9aa3ad).fillRect(px, py - 8, w * TILE, TILE + 8);
          for (let i = 0; i < 3; i++) g.fillStyle(0x6d7682).fillRect(px + 2, py - 6 + i * 8, w * TILE - 4, 1).fillRect(px + w * TILE / 2 - 3, py - 3 + i * 8, 6, 2);
          break;
        case "printer":
          g.fillStyle(0x2E2C29).fillRect(px + 2, py - 3, w * TILE - 4, TILE + 3);
          g.fillStyle(0xe8eef2).fillRect(px + 3, py - 2, w * TILE - 6, TILE + 1);
          g.fillStyle(0xf6f5f0).fillRect(px + 8, py - 6, w * TILE - 16, 5);
          g.fillStyle(0x3a9a7a).fillRect(px + w * TILE - 9, py + 2, 3, 2);
          break;
        case "chalkboard": {   // the Quant Lab's board: a bell curve and a DCF sketch
          g.fillStyle(0x6b4a2e).fillRect(px, py - 12, w * TILE, TILE + 4);
          g.fillStyle(0x2f4a3a).fillRect(px + 2, py - 10, w * TILE - 4, TILE);
          g.lineStyle(1, 0xf6f5f0).beginPath();
          for (let i = 0; i <= 30; i++) {
            const xx = px + 6 + i, yy = py + 3 - 11 * Math.exp(-((i - 15) ** 2) / 40);
            if (i === 0) g.moveTo(xx, yy); else g.lineTo(xx, yy);
          }
          g.strokePath();
          g.fillStyle(0xc9a227).fillRect(px + 44, py - 6, 2, 8).fillRect(px + 50, py - 4, 2, 6).fillRect(px + 56, py - 8, 2, 10).fillRect(px + 62, py - 2, 2, 4);
          break;
        }
        case "server":
          g.fillStyle(0x2E2C29).fillRect(px + 1, py - 14, w * TILE - 2, TILE + 14);
          g.fillStyle(0x46505e).fillRect(px + 2, py - 13, w * TILE - 4, TILE + 12);
          for (let i = 0; i < 5; i++) g.fillStyle(i % 2 ? 0x3a9a7a : 0x7fd6ff).fillRect(px + 5, py - 10 + i * 5, 2, 2).fillRect(px + 9, py - 10 + i * 5, w * TILE - 14, 1);
          break;
        case "rug":
          g.fillStyle(0x8e2a2a).fillRect(px, py, w * TILE, h * TILE);
          g.fillStyle(0xc9a227).fillRect(px + 2, py + 2, w * TILE - 4, 1).fillRect(px + 2, py + h * TILE - 3, w * TILE - 4, 1);
          break;
      }
    }
    // meeting table
    const t = TABLE;
    const g = this.add.graphics().setDepth((t.y + t.h) * TILE);
    g.fillStyle(0x2E2C29).fillRoundedRect(t.x * TILE - 1, t.y * TILE - 1, t.w * TILE + 2, t.h * TILE + 2, 6);
    g.fillStyle(0x9a6f45).fillRoundedRect(t.x * TILE, t.y * TILE, t.w * TILE, t.h * TILE, 6);
    g.fillStyle(0xb98a5a).fillRoundedRect(t.x * TILE + 2, t.y * TILE + 2, t.w * TILE - 4, t.h * TILE - 6, 5);
    g.fillStyle(0xf6f5f0).fillRect(t.x * TILE + 20, t.y * TILE + 12, 8, 6).fillRect(t.x * TILE + 70, t.y * TILE + 20, 8, 6);
    // lobby chairs (seats) under sitters
    const s = this.add.graphics().setDepth(2);
    for (const seat of SEATS) {
      s.fillStyle(0x2E2C29).fillRect(seat.x * TILE + 2, seat.y * TILE + 6, TILE - 4, TILE - 6);
      s.fillStyle(0x8a6db8).fillRect(seat.x * TILE + 3, seat.y * TILE + 7, TILE - 6, TILE - 8);
    }
  }
}
