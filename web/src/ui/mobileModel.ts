// Decisions behind the iPhone layout, kept free of the DOM so they can be tested on their own.

/** The phone layout is for visitors on a narrow screen (iPhone in portrait is 375 to 440 wide).
 *  The Captain's tools are not designed for phones yet, so a signed-in Captain keeps the desktop
 *  layout. `?mobile=1` forces it on, for testing from a desktop browser. */
export const MOBILE_MAX_WIDTH = 640;
export function isMobileLayout(o: { width: number; visitor: boolean; forced?: boolean }): boolean {
  return !!o.forced || (o.visitor && o.width <= MOBILE_MAX_WIDTH);
}

export type MTab = "floor" | "feed" | "portfolio" | "more";
export const MTABS: { id: MTab; label: string }[] = [
  { id: "floor", label: "Floor" },
  { id: "feed", label: "Feed" },
  { id: "portfolio", label: "Portfolio" },
  { id: "more", label: "More" },
];

/** What each row under More opens. `panel` names an existing sidebar panel; the others are actions. */
export type MoreId = "watchlist" | "news" | "agent" | "demo" | "contact" | "appearance";
export interface MoreRow { id: MoreId; label: string; hint: string; kind: "panel" | "action" | "link" }
export const MORE_ROWS: MoreRow[] = [
  { id: "watchlist", label: "Watchlist", hint: "Names we are watching, and how they have done", kind: "panel" },
  { id: "news", label: "Newsletters", hint: "Notes we have published", kind: "panel" },
  { id: "agent", label: "Team", hint: "Who is who in the office", kind: "panel" },
  { id: "demo", label: "Demo", hint: "Watch the office build a report in five minutes", kind: "action" },
  { id: "appearance", label: "Appearance", hint: "Light or dark", kind: "action" },
  { id: "contact", label: "Contact", hint: "Email us", kind: "link" },
];

/** The big title under the masthead. The Floor needs every pixel, so it gets none. */
export function titleFor(tab: MTab, sub: MoreId | null): string {
  if (sub) return MORE_ROWS.find((r) => r.id === sub)?.label ?? "More";
  return tab === "floor" ? "" : MTABS.find((t) => t.id === tab)!.label;
}

/** The one-time "Add to Home Screen" hint: only on an iPhone, only in the browser (not once it
 *  is installed), only after the visitor has stayed a while, and never again once dismissed. */
export function installHintDue(o: { iphone: boolean; standalone: boolean; dismissed: boolean; secondsOpen: number }): boolean {
  return o.iphone && !o.standalone && !o.dismissed && o.secondsOpen >= 15;
}

export function isIphone(userAgent: string): boolean {
  return /iPhone/.test(userAgent);
}

// ---- Floor views: swipe order, swipe detection, the quiet-state home ------------------------------

/** Chips along the top of the Floor, in swipe order. */
export const FLOOR_VIEWS = ["floor", "equity_research", "screening", "quant", "audit", "client_relations", "lobby"];

/** The view a swipe lands on, or null at either end (no wrapping: the chip row is a line). */
export function neighbourView(order: string[], current: string, dir: -1 | 1): string | null {
  const i = order.indexOf(current);
  if (i < 0) return null;
  return order[i + dir] ?? null;
}

/** -1 = swipe right (go to the previous view), 1 = swipe left (next), 0 = not a deliberate sideways swipe. */
export function swipeDir(o: { dx: number; dy: number; ms: number }): -1 | 0 | 1 {
  if (o.ms > 800 || Math.abs(o.dx) < 48 || Math.abs(o.dx) < Math.abs(o.dy) * 1.6) return 0;
  return o.dx < 0 ? 1 : -1;
}

/** A tap is a small movement; a swipe or a pan must not count as a tap on whoever is underneath. */
export const TAP_SLOP = 12;

/** On first opening: the floor if anyone is working, otherwise the summary home. */
export function opensOnHome(o: { working: number; replaying: boolean; touring: boolean }): boolean {
  return o.working === 0 && !o.replaying && !o.touring;
}

/** "just now", "12 minutes ago", "3 hours ago", "yesterday", then the date. */
export function lastActiveLabel(ts: number | null, nowMs = Date.now()): string {
  if (!ts) return "No recent activity";
  const s = Math.max(0, nowMs / 1000 - ts);
  if (s < 90) return "Active just now";
  if (s < 3600) return `Last active ${Math.round(s / 60)} minutes ago`;
  if (s < 86400) { const h = Math.round(s / 3600); return `Last active ${h} ${h === 1 ? "hour" : "hours"} ago`; }
  if (s < 2 * 86400) return "Last active yesterday";
  return `Last active ${new Date(ts * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`;
}
