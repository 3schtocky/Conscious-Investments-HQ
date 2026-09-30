// Floor plan of Conscious Investments HQ, in 16 px tiles.
//
//   ┌──────────┬──────────┬──────────┬──────────┐
//   │ Equity   │Screening │  Audit   │ Client   │   wings (2 desks each)
//   │ Research │          │          │Relations │
//   ├───door───┴───door───┴───door───┴───door───┤
//   │                 L O B B Y                 │   meeting table + seats
//   ├────────door─────────┬────────door─────────┤
//   │  Executive Suite    │  Captain's Office   │
//   └─────────────────────┴─────────────────────┘

export const TILE = 16;
export const COLS = 49;
export const ROWS = 30;

export type Rect = { x: number; y: number; w: number; h: number };
export type Pt = { x: number; y: number };

export interface Room {
  id: string;            // wing id, "lobby", "captain"
  label: string;
  rect: Rect;            // interior, in tiles
  floor: [string, string]; // two-tone floor pattern
  accent: string;
}

export interface Desk {
  owner: string;         // agent id or "captain"
  chair: Pt;             // where the owner sits (facing down, toward the viewer)
  visitor: Pt;           // where a visitor stands, in front of the desk
  room: string;
}

export const WING_ACCENT: Record<string, string> = {
  equity_research: "#2a68a8",
  screening: "#d27030",
  audit: "#8a6db8",
  client_relations: "#3a9a7a",
  executive: "#2E2C29",
  lobby: "#B9B5AD",
  captain: "#b8892f",
};

export const ROOMS: Room[] = [
  { id: "equity_research", label: "Equity Research", rect: { x: 1, y: 1, w: 11, h: 10 },
    floor: ["#dfe7f0", "#d4dde9"], accent: WING_ACCENT.equity_research },
  { id: "screening", label: "Screening", rect: { x: 13, y: 1, w: 11, h: 10 },
    floor: ["#f3e4d6", "#ecdac8"], accent: WING_ACCENT.screening },
  { id: "audit", label: "Audit", rect: { x: 25, y: 1, w: 11, h: 10 },
    floor: ["#e8e2f1", "#ddd5ea"], accent: WING_ACCENT.audit },
  { id: "client_relations", label: "Client Relations", rect: { x: 37, y: 1, w: 11, h: 10 },
    floor: ["#dcefe7", "#cfe6dc"], accent: WING_ACCENT.client_relations },
  { id: "lobby", label: "Lobby", rect: { x: 1, y: 12, w: 47, h: 9 },
    floor: ["#efe9dc", "#e6dfcf"], accent: WING_ACCENT.lobby },
  { id: "executive", label: "Executive Suite", rect: { x: 1, y: 22, w: 23, h: 7 },
    floor: ["#d9cbb5", "#cfc0a8"], accent: WING_ACCENT.executive },
  { id: "captain", label: "Captain's Office", rect: { x: 25, y: 22, w: 23, h: 7 },
    floor: ["#caa9a0", "#c09e94"], accent: WING_ACCENT.captain },
];

// Door gaps (2 tiles wide) in the horizontal walls at y = 11 and y = 21.
export const DOORS: Pt[] = [
  { x: 6, y: 11 }, { x: 7, y: 11 }, { x: 18, y: 11 }, { x: 19, y: 11 },
  { x: 30, y: 11 }, { x: 31, y: 11 }, { x: 42, y: 11 }, { x: 43, y: 11 },
  { x: 12, y: 21 }, { x: 13, y: 21 }, { x: 36, y: 21 }, { x: 37, y: 21 },
];

const WING_ORDER = ["equity_research", "screening", "audit", "client_relations"];

/** Desks: lead on the left, associate on the right of each wing; Juno and the Captain below. */
export function buildDesks(agents: { id: string; wing: string; tier: string }[]): Desk[] {
  const desks: Desk[] = [];
  for (const a of agents) {
    if (a.wing === "executive") {
      desks.push({ owner: a.id, chair: { x: 8, y: 24 }, visitor: { x: 8, y: 27 }, room: "executive" });
      continue;
    }
    const i = WING_ORDER.indexOf(a.wing);
    if (i < 0) continue;
    const room = ROOMS.find((r) => r.id === a.wing)!.rect;
    const cx = room.x + (a.tier === "lead" ? 3 : 7);
    desks.push({ owner: a.id, chair: { x: cx, y: room.y + 3 }, visitor: { x: cx, y: room.y + 6 }, room: a.wing });
  }
  desks.push({ owner: "captain", chair: { x: 36, y: 24 }, visitor: { x: 36, y: 27 }, room: "captain" });
  return desks;
}

// Lobby meeting table (tiles) and the seats around it.
export const TABLE: Rect = { x: 21, y: 15, w: 7, h: 3 };
export const SEATS: Pt[] = [
  ...[21, 23, 25, 27].map((x) => ({ x, y: 14 })),
  ...[21, 23, 25, 27].map((x) => ({ x, y: 18 })),
  { x: 20, y: 16 }, { x: 28, y: 16 },
];

// Furniture that blocks walking (besides desks and the table), as tile rects.
export const PROPS: { kind: string; rect: Rect }[] = [
  { kind: "couch", rect: { x: 4, y: 14, w: 4, h: 2 } },
  { kind: "couch", rect: { x: 41, y: 14, w: 4, h: 2 } },
  { kind: "plant", rect: { x: 1, y: 12, w: 1, h: 1 } },
  { kind: "plant", rect: { x: 47, y: 12, w: 1, h: 1 } },
  { kind: "plant", rect: { x: 1, y: 20, w: 1, h: 1 } },
  { kind: "plant", rect: { x: 47, y: 20, w: 1, h: 1 } },
  { kind: "cooler", rect: { x: 33, y: 13, w: 1, h: 1 } },
  { kind: "reception", rect: { x: 14, y: 18, w: 4, h: 1 } },
  { kind: "bookshelf", rect: { x: 1, y: 1, w: 2, h: 1 } },
  { kind: "bookshelf", rect: { x: 10, y: 1, w: 2, h: 1 } },
  { kind: "whiteboard", rect: { x: 16, y: 1, w: 5, h: 1 } },
  { kind: "cabinet", rect: { x: 25, y: 1, w: 2, h: 1 } },
  { kind: "cabinet", rect: { x: 34, y: 1, w: 2, h: 1 } },
  { kind: "printer", rect: { x: 46, y: 1, w: 2, h: 1 } },
  { kind: "plant", rect: { x: 37, y: 1, w: 1, h: 1 } },
  { kind: "bookshelf", rect: { x: 1, y: 22, w: 3, h: 1 } },
  { kind: "plant", rect: { x: 23, y: 22, w: 1, h: 1 } },
  { kind: "bookshelf", rect: { x: 44, y: 22, w: 4, h: 1 } },
  { kind: "rug", rect: { x: 32, y: 26, w: 9, h: 2 } },
  { kind: "plant", rect: { x: 25, y: 28, w: 1, h: 1 } },
];

/** Walkable grid: true = walkable. */
export function walkable(desks: Desk[]): boolean[][] {
  const g: boolean[][] = Array.from({ length: ROWS }, () => Array(COLS).fill(false));
  for (const r of ROOMS) {
    for (let y = r.rect.y; y < r.rect.y + r.rect.h; y++)
      for (let x = r.rect.x; x < r.rect.x + r.rect.w; x++) g[y][x] = true;
  }
  for (const d of DOORS) g[d.y][d.x] = true;
  const block = (rc: Rect) => {
    for (let y = rc.y; y < rc.y + rc.h; y++) for (let x = rc.x; x < rc.x + rc.w; x++) if (g[y]) g[y][x] = false;
  };
  for (const p of PROPS) if (p.kind !== "rug") block(p.rect);
  block(TABLE);
  for (const d of desks) block({ x: d.chair.x - 1, y: d.chair.y + 1, w: 3, h: 1 });
  return g;
}

export function roomAt(p: Pt): Room | undefined {
  return ROOMS.find((r) => p.x >= r.rect.x && p.x < r.rect.x + r.rect.w && p.y >= r.rect.y && p.y < r.rect.y + r.rect.h);
}
