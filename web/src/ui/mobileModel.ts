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
