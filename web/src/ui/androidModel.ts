// What differs on Android, kept free of the DOM so it can be tested: telling the platforms apart,
// which install hint to give, and how the system back button should walk back through the app.

export type Platform = "ios" | "android" | "other";
export const platformOf = (ua: string): Platform => (/iPhone|iPad|iPod/.test(ua) ? "ios" : /Android/.test(ua) ? "android" : "other");
export const isSamsungBrowser = (ua: string): boolean => /SamsungBrowser/.test(ua);

/** The one-time "Add to Home Screen" card. Only on a phone, only in the browser (not once installed),
 *  only after the visitor has stayed a while, and never again once dismissed. On Android, Chrome and
 *  Samsung Internet can show the real install dialog from our own button when they have offered it;
 *  otherwise the card says where the browser's menu entry is. */
export type InstallKind = "ios-share" | "android-prompt" | "android-menu" | "samsung-menu";
export function installPlan(o: { platform: Platform; standalone: boolean; dismissed: boolean; secondsOpen: number; canPrompt: boolean; samsung: boolean }): InstallKind | null {
  if (o.platform === "other" || o.standalone || o.dismissed || o.secondsOpen < 15) return null;
  if (o.platform === "ios") return "ios-share";
  if (o.canPrompt) return "android-prompt";
  return o.samsung ? "samsung-menu" : "android-menu";
}

// ---- the back button ---------------------------------------------------------------------------------
/** Where the visitor is, as far as "back" is concerned. */
export interface NavState { sheet: boolean; tab: "floor" | "feed" | "portfolio" | "more"; sub: string | null; fileOpen: boolean }
export type BackStep = "sheet" | "file" | "sub" | "floor";

/** What one press of Back does: close the agent sheet, then leave a reader page, then go from a More
 *  page back to the More list, then from any other tab back to the Floor. From the Floor it does
 *  nothing here (null): the browser leaves the app. */
export function nextBack(s: NavState): BackStep | null {
  if (s.sheet) return "sheet";
  if (s.tab === "more" && s.sub === "demo" && s.fileOpen) return "file";
  if (s.tab === "more" && s.sub) return "sub";
  if (s.tab !== "floor") return "floor";
  return null;
}

/** How many Back presses stay inside the app. */
export function backDepth(s: NavState): number {
  let n = 0, cur = { ...s };
  for (let step = nextBack(cur); step; step = nextBack(cur)) {
    n++;
    if (step === "sheet") cur = { ...cur, sheet: false };
    else if (step === "file") cur = { ...cur, fileOpen: false };
    else if (step === "sub") cur = { ...cur, sub: null };
    else cur = { ...cur, tab: "floor", sub: null };
  }
  return n;
}

/** Keep one browser history entry per layer: push when the visitor goes deeper, step back when the
 *  app closed a layer by itself (a Close button, say) so the Back button doesn't land on a stale one. */
export function planHistory(tracked: number, depth: number): { push: number; pop: number } {
  return { push: Math.max(0, depth - tracked), pop: Math.max(0, tracked - depth) };
}
