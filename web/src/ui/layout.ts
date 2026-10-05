// Floor text that respects its neighbours: name tags and speech bubbles of colleagues who stand
// side by side must not sit on top of each other. Pure functions (no DOM), so they can be tested
// on their own; overlay.ts feeds them measured sizes every frame.

export interface TagBox { id: string; x: number; w: number; lane: number }   // x = centre; lane = last frame's

/** Which row each name tag sits on. Lane 0 is the usual spot over the head; a tag that would
 *  overlap another on its row steps up a lane. Tags keep last frame's order, so someone standing
 *  still never swaps places with a neighbour. */
export function tagLanes(items: TagBox[], gap = 3): Map<string, number> {
  const rows: TagBox[][] = [];
  const out = new Map<string, number>();
  const order = [...items].sort((a, b) => a.lane - b.lane || a.x - b.x || a.id.localeCompare(b.id));
  for (const it of order) {
    const clear = (row: TagBox[]) => row.every((o) => Math.abs(o.x - it.x) >= (o.w + it.w) / 2 + gap);
    let k = rows.findIndex(clear);
    if (k < 0) { k = rows.length; rows.push([]); }
    rows[k].push(it);
    out.set(it.id, k);
  }
  return out;
}

export interface BubbleBox {
  id: string;
  x: number;       // speaker's x (centre of the head)
  y: number;       // speaker's head y
  w: number;
  h: number;
  below: boolean;  // drawn under the head instead of above (near the top of the view)
  lift: number;    // px already clear of the head (the speaker's own name-tag lane)
}
export interface BubblePlace { cx: number; lift: number }   // final centre x, and lift away from the head

const NEAR = 20;    // bubble bottom sits this far above the head anchor (clears the name tag)
const BELOW = 16;   // ...or this far under it
const GAP = 6;
const EDGE = 6;

interface Rc { l: number; r: number; t: number; b: number }

function rect(b: BubbleBox, cx: number, lift: number): Rc {
  const bottom = b.below ? b.y + BELOW + lift + b.h : b.y - NEAR - lift;
  const top = b.below ? b.y + BELOW + lift : bottom - b.h;
  return { l: cx - b.w / 2, r: cx + b.w / 2, t: top, b: bottom };
}
/** Top edge of a bubble once placed (negative = above the top of the view). */
export function bubbleTop(b: BubbleBox, p: BubblePlace): number { return rect(b, p.cx, p.lift).t; }
const hit = (a: Rc, c: Rc) => a.l < c.r + GAP && c.l < a.r + GAP && a.t < c.b + GAP && c.t < a.b + GAP;

/** Spread simultaneous bubbles so none covers another. Bubbles that touch are grouped, laid out
 *  left to right around the group's average spot (so each stays close to its speaker; the tail
 *  keeps pointing at them), and kept inside [min, max]. If a group is too wide for the view, the
 *  overflow goes onto a second row above (or below) the first. */
export function placeBubbles(items: BubbleBox[], min: number, max: number): Map<string, BubblePlace> {
  const place = new Map<string, BubblePlace>();
  const clamp = (b: BubbleBox, cx: number) => Math.max(min + EDGE + b.w / 2, Math.min(max - EDGE - b.w / 2, cx));
  for (const b of items) place.set(b.id, { cx: clamp(b, b.x), lift: b.lift });

  for (let pass = 0; pass < 6; pass++) {
    const rc = (b: BubbleBox) => rect(b, place.get(b.id)!.cx, place.get(b.id)!.lift);
    // connected groups of touching bubbles (a handful of items, so plain relabelling is fine)
    const label = items.map((_, i) => i);
    for (let i = 0; i < items.length; i++) {
      for (let j = i + 1; j < items.length; j++) {
        if (!hit(rc(items[i]), rc(items[j])) || label[i] === label[j]) continue;
        const from = label[j];
        for (let k = 0; k < label.length; k++) if (label[k] === from) label[k] = label[i];
      }
    }
    const members = new Map<number, BubbleBox[]>();
    items.forEach((b, i) => members.set(label[i], [...(members.get(label[i]) ?? []), b]));

    let moved = false;
    for (const g of members.values()) {
      if (g.length < 2) continue;
      moved = true;
      const sorted = [...g].sort((a, b) => a.x - b.x || a.id.localeCompare(b.id));
      // fill rows no wider than the view
      const rows: BubbleBox[][] = [[]];
      let width = 0;
      for (const b of sorted) {
        const need = b.w + (rows[rows.length - 1].length ? GAP : 0);
        if (rows[rows.length - 1].length && width + need > max - min - 2 * EDGE) { rows.push([]); width = 0; }
        width += b.w + (rows[rows.length - 1].length ? GAP : 0);
        rows[rows.length - 1].push(b);
      }
      let tier = 0;
      for (const row of rows) {
        const total = row.reduce((s, b) => s + b.w, 0) + GAP * (row.length - 1);
        const mean = row.reduce((s, b) => s + b.x, 0) / row.length;
        const start = Math.max(min + EDGE, Math.min(max - EDGE - total, mean - total / 2));
        let at = start;
        for (const b of row) {
          place.set(b.id, { cx: at + b.w / 2, lift: b.lift + tier });
          at += b.w + GAP;
        }
        tier += Math.max(...row.map((b) => b.h)) + GAP;
      }
    }
    if (!moved) break;
  }
  return place;
}
