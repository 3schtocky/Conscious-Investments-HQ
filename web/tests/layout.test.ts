// Run with: npm test   (Node's built-in runner; no extra packages)
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildDesks, roomAt, visitSlots, walkable } from "../src/office/map.ts";
import { placeBubbles, tagLanes, type BubbleBox } from "../src/ui/layout.ts";

const AGENTS = [
  ...["equity_research", "screening", "audit", "client_relations", "quant"].flatMap((wing) => [
    { id: `${wing}-lead`, wing, tier: "lead" }, { id: `${wing}-assoc`, wing, tier: "associate" }]),
  { id: "juno", wing: "executive", tier: "associate" },
];
const desks = buildDesks(AGENTS);
const grid = walkable(desks);

test("every desk has room for a crowd, all of it walkable and inside its own room", () => {
  for (const d of desks) {
    const slots = visitSlots(d, grid);
    assert.ok(slots.length >= 8, `${d.owner}: only ${slots.length} spots`);
    assert.deepEqual(slots[0], d.visitor, `${d.owner}: the first choice is the usual spot`);
    assert.equal(new Set(slots.map((p) => `${p.x},${p.y}`)).size, slots.length, `${d.owner}: duplicate spot`);
    for (const p of slots) {
      assert.ok(grid[p.y][p.x], `${d.owner}: spot ${p.x},${p.y} is not walkable`);
      assert.equal(roomAt(p)?.id, d.room);
    }
  }
});

test("the first spots at a desk sit side by side, not stacked", () => {
  const d = desks.find((x) => x.owner === "equity_research-lead")!;
  const front = visitSlots(d, grid).slice(0, 5);
  assert.ok(front.every((p) => p.y === d.visitor.y), "a crowd of five stands in one row");
  assert.deepEqual(front.map((p) => p.x).sort((a, b) => a - b), [-2, -1, 0, 1, 2].map((dx) => d.visitor.x + dx));
});

test("a wing's two desks keep their first four spots apart", () => {
  for (const wing of ["equity_research", "screening", "audit", "client_relations", "quant"]) {
    const spots = desks.filter((d) => d.room === wing).flatMap((d) => visitSlots(d, grid).slice(0, 4).map((p) => `${p.x},${p.y}`));
    assert.equal(new Set(spots).size, spots.length, `${wing}: two desks share a first-choice spot`);
  }
});

test("name tags of neighbours step into separate lanes and return when apart", () => {
  const side = tagLanes([
    { id: "a", x: 100, w: 50, lane: 0 }, { id: "b", x: 121, w: 50, lane: 0 }, { id: "c", x: 142, w: 50, lane: 0 }]);
  assert.equal(new Set(side.values()).size, 3);
  const apart = tagLanes([
    { id: "a", x: 100, w: 50, lane: 0 }, { id: "b", x: 300, w: 50, lane: 1 }, { id: "c", x: 500, w: 50, lane: 2 }]);
  assert.deepEqual([...apart.values()], [0, 0, 0]);
});

test("a tag that already holds a lane keeps it while its neighbour stands still", () => {
  const items = [{ id: "a", x: 100, w: 50, lane: 0 }, { id: "b", x: 121, w: 50, lane: 1 }];
  for (let i = 0; i < 5; i++) {
    const l = tagLanes(items);
    assert.equal(l.get("a"), 0);
    assert.equal(l.get("b"), 1);
  }
});

const rc = (b: BubbleBox, cx: number, lift: number) => {
  const bottom = b.below ? b.y + 16 + lift + b.h : b.y - 20 - lift;
  return { l: cx - b.w / 2, r: cx + b.w / 2, t: b.below ? b.y + 16 + lift : bottom - b.h, b: bottom };
};
const overlap = (p: ReturnType<typeof rc>, q: ReturnType<typeof rc>) => p.l < q.r && q.l < p.r && p.t < q.b && q.t < p.b;
const box = (id: string, x: number, w = 200, h = 44, y = 300): BubbleBox => ({ id, x, y, w, h, below: false, lift: 0 });

test("bubbles from three colleagues side by side do not cover each other", () => {
  const items = [box("a", 400), box("b", 416), box("c", 432)];
  const out = placeBubbles(items, 0, 1200);
  for (const i of items) for (const j of items) {
    if (i.id < j.id) assert.ok(!overlap(rc(i, out.get(i.id)!.cx, out.get(i.id)!.lift), rc(j, out.get(j.id)!.cx, out.get(j.id)!.lift)), `${i.id} covers ${j.id}`);
  }
  const xs = items.map((i) => out.get(i.id)!.cx);
  assert.ok(xs[0] < xs[1] && xs[1] < xs[2], "left-to-right order is kept so each tail still points at its speaker");
  assert.ok(Math.abs((xs[0] + xs[2]) / 2 - 416) < 1, "the group stays centred on the speakers");
});

test("a lone bubble stays over its speaker, and inside the view at the edges", () => {
  const mid = placeBubbles([box("a", 500)], 0, 1200).get("a")!;
  assert.equal(mid.cx, 500);
  assert.equal(mid.lift, 0);
  assert.ok(placeBubbles([box("a", 10)], 0, 1200).get("a")!.cx >= 106);
  assert.ok(placeBubbles([box("a", 1195)], 0, 1200).get("a")!.cx <= 1094);
});

test("when the view is too narrow for everyone, the overflow goes on a second row", () => {
  const items = ["a", "b", "c", "d", "e"].map((id, i) => box(id, 300 + i * 16, 240));
  const out = placeBubbles(items, 0, 600);
  for (const i of items) {
    const p = out.get(i.id)!;
    assert.ok(p.cx - 120 >= 0 && p.cx + 120 <= 600, `${i.id} left the view`);
    for (const j of items) if (i.id < j.id) assert.ok(!overlap(rc(i, p.cx, p.lift), rc(j, out.get(j.id)!.cx, out.get(j.id)!.lift)), `${i.id} covers ${j.id}`);
  }
  assert.ok([...out.values()].some((p) => p.lift > 0), "someone moved up a row");
});

test("bubbles far apart are left alone", () => {
  const out = placeBubbles([box("a", 200), box("b", 900)], 0, 1200);
  assert.equal(out.get("a")!.cx, 200);
  assert.equal(out.get("b")!.cx, 900);
});
