// Tiny DOM helpers (no framework).
type Attrs = Record<string, string | number | boolean | EventListener | undefined>;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K, attrs: Attrs = {}, ...children: (Node | string | null | undefined | false)[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v as EventListener);
    else if (k === "class") el.className = String(v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) el.append(c);
  return el;
}

export function clear(el: HTMLElement) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

export function timeAgo(ts: number): string {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** Costs are shown to the cent; the ledger itself keeps full precision for the cap. */
/** Emojis stay in the sidebar only; text boxes on the floor (bubbles, desk cards) are plain. */
const EMOJI = /[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}\uFE0F\u200D\u20E3]/gu;
export function plain(s: string): string {
  return s.replace(EMOJI, "").replace(/[ \t]{2,}/g, " ").trim();
}

/** Download link for an attachment path like "quant/META_model_v1.xlsx" or "brief.md". */
export function fileHref(path: string, ticker?: string | null): string | null {
  if (!ticker) return null;
  if (path.startsWith("/files/")) return path;
  return path.startsWith("quant/") ? `/files/quant/${ticker}/${path.slice(6)}` : `/files/coverage/${ticker}/${path}`;
}

export function money(n: number): string {
  return `$${n.toFixed(2)}`;
}
