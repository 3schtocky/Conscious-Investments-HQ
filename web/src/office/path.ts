// A* on the tile grid (4-neighbour). Small grid, so a simple open list is plenty fast.
import type { Pt } from "./map";

export function findPath(grid: boolean[][], from: Pt, to: Pt): Pt[] {
  const rows = grid.length, cols = grid[0].length;
  const key = (p: Pt) => p.y * cols + p.x;
  const ok = (p: Pt) => p.x >= 0 && p.y >= 0 && p.x < cols && p.y < rows && grid[p.y][p.x];
  const goal = key(to);
  const h = (p: Pt) => Math.abs(p.x - to.x) + Math.abs(p.y - to.y);
  const open: { p: Pt; f: number }[] = [{ p: from, f: h(from) }];
  const came = new Map<number, number>();
  const g = new Map<number, number>([[key(from), 0]]);
  const seen = new Set<number>();
  while (open.length) {
    open.sort((a, b) => a.f - b.f);
    const { p } = open.shift()!;
    const k = key(p);
    if (k === goal) {
      const path: Pt[] = [];
      let c: number | undefined = k;
      while (c !== undefined && c !== key(from)) {
        path.push({ x: c % cols, y: Math.floor(c / cols) });
        c = came.get(c);
      }
      return path.reverse();
    }
    if (seen.has(k)) continue;
    seen.add(k);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const n = { x: p.x + dx, y: p.y + dy };
      // The goal may be a chair tile that is otherwise fine; walls are never ok.
      if (!ok(n) && key(n) !== goal) continue;
      const nk = key(n);
      const cost = g.get(k)! + 1;
      if (cost < (g.get(nk) ?? Infinity)) {
        g.set(nk, cost);
        came.set(nk, k);
        open.push({ p: n, f: cost + h(n) });
      }
    }
  }
  return [];
}
