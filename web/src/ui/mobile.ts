// The iPhone shell: a masthead on top, the content, and a bottom tab bar (Floor, Feed, Portfolio,
// More). It reuses the office's existing panels and map, so it only decides what is on screen;
// main.ts owns the data and the panels. Desktop is untouched: everything here sits behind
// `body.mobile` (see mobile.css).
import { h } from "./dom";
import { tap } from "./haptics";
import { installHintDue, isIphone, MORE_ROWS, MTABS, titleFor, type MoreId, type MTab } from "./mobileModel";

export type PanelTab = "activity" | "portfolio" | "watchlist" | "news" | "agent";

export interface MobileHooks {
  statusPill: HTMLElement;          // the office status pill: lives in the masthead on a phone
  desktopHeader: HTMLElement;       // ...and goes back here on a wide screen
  showPanel: (tab: PanelTab) => void;
  startDemo: () => void;
  toggleTheme: () => void;
  themeLabel: () => string;
  contactHref: string;
}

const ICONS: Record<MTab, string> = {
  floor: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 11h17M12 11v8.5"/>',
  feed: '<path d="M5 7h14M5 12h14M5 17h9"/>',
  portfolio: '<path d="M4 19V5M4 19h16M7.5 15l3.5-4 3 3 5-6.5"/>',
  more: '<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18" cy="12" r="1.3"/>',
};
const svg = (inner: string) => {
  const el = document.createElement("span");
  el.className = "m-icon";
  el.innerHTML = `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  return el;
};

const HINT_KEY = "hq-install-hint";

export class MobileShell {
  tab: MTab = "floor";
  sub: MoreId | null = null;
  active = false;
  private head = h("div", { class: "m-head" });
  private statusSlot = h("span", { class: "m-status" });
  private back = h("button", { class: "m-back hidden", "aria-label": "Back to More", onclick: () => this.go("more", null, true) }, "‹ More");
  private title = h("h1", { class: "m-title" });
  private bar = h("nav", { class: "m-tabs", "aria-label": "Sections" });
  private buttons = new Map<MTab, HTMLButtonElement>();
  private more = h("div", { class: "m-more" });
  private themeValue = h("span", { class: "m-row-value" });
  private hint = h("div", { class: "m-hint hidden", role: "note" });
  private hintTimer = 0;
  private opened = Date.now();

  constructor(app: HTMLElement, main: HTMLElement, private hooks: MobileHooks) {
    this.head.append(
      h("div", { class: "m-bar" }, this.back, h("span", { class: "m-brand" }, h("span", { class: "m-mark" }), h("span", { class: "m-word" }, "Conscious Investments ", h("b", {}, "HQ"))), this.statusSlot),
      this.title);
    for (const t of MTABS) {
      const b = h("button", { class: "m-tab", "aria-label": t.label, onclick: () => this.press(t.id) }, svg(ICONS[t.id]), h("span", { class: "m-tab-label" }, t.label));
      this.buttons.set(t.id, b);
      this.bar.append(b);
    }
    this.buildMore();
    this.buildHint();
    app.prepend(this.head);
    main.append(this.more);   // inside <main>, so it takes exactly the space the floor and panels do
    app.append(this.hint, this.bar);
  }

  /** Switch the phone layout on or off (the screen can rotate or resize into either). */
  setActive(on: boolean) {
    if (on === this.active) return;
    this.active = on;
    document.body.classList.toggle("mobile", on);
    if (on) {
      this.statusSlot.append(this.hooks.statusPill);
      this.go(this.tab, this.sub);
      this.hintTimer = window.setInterval(() => this.maybeHint(), 3000);
    } else {
      this.hooks.desktopHeader.insertBefore(this.hooks.statusPill, this.hooks.desktopHeader.children[1] ?? null);
      document.body.removeAttribute("data-mtab");
      document.body.removeAttribute("data-msub");
      window.clearInterval(this.hintTimer);
      this.hint.classList.add("hidden");
    }
  }

  private press(tab: MTab) {
    tap();
    if (tab === this.tab) {   // tapping the open tab goes back to its top, as iOS apps do
      if (tab === "more" && this.sub) this.go("more", null, true);
      else this.scrollTop();
      return;
    }
    this.go(tab, null, true);
  }

  private scrollTop() {
    document.querySelectorAll<HTMLElement>(".panel > *, .m-more").forEach((el) => { el.scrollTop = 0; });
  }

  go(tab: MTab, sub: MoreId | null = null, _user = false) {
    this.tab = tab;
    this.sub = tab === "more" ? sub : null;
    document.body.dataset.mtab = tab;
    document.body.dataset.msub = this.sub ?? "";
    const panel: PanelTab | null = tab === "feed" ? "activity" : tab === "portfolio" ? "portfolio"
      : this.sub === "watchlist" || this.sub === "news" || this.sub === "agent" ? this.sub : null;
    if (panel) this.hooks.showPanel(panel);
    for (const [id, b] of this.buttons) {
      b.classList.toggle("active", id === tab);
      if (id === tab) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    }
    const t = titleFor(tab, this.sub);
    this.title.textContent = t;
    this.title.classList.toggle("hidden", !t);
    this.back.classList.toggle("hidden", !this.sub);
    this.head.classList.toggle("has-back", !!this.sub);
    this.themeValue.textContent = this.hooks.themeLabel();
  }

  private buildMore() {
    const rows = MORE_ROWS.map((r) => {
      const value = r.id === "appearance" ? this.themeValue : null;
      const inner = [h("span", { class: "m-row-main" }, h("span", { class: "m-row-label" }, r.label), h("span", { class: "m-row-hint" }, r.hint)), value, h("span", { class: "m-row-chev", "aria-hidden": "true" }, "›")];
      const open = () => {
        tap();
        if (r.id === "demo") { this.go("more", "demo"); this.hooks.startDemo(); }
        else if (r.id === "appearance") { this.hooks.toggleTheme(); this.themeValue.textContent = this.hooks.themeLabel(); }
        else this.go("more", r.id);
      };
      return r.kind === "link"
        ? h("a", { class: "m-row", href: this.hooks.contactHref, onclick: () => tap() }, ...inner)
        : h("button", { class: "m-row", onclick: open }, ...inner);
    });
    this.more.append(h("div", { class: "m-group" }, ...rows));
  }

  // ---- the one-time "Add to Home Screen" hint -------------------------------------------------
  private buildHint() {
    this.hint.append(
      h("span", { class: "m-hint-text" }, "Add this to your Home Screen: tap ", h("b", {}, "Share"), ", then ", h("b", {}, "Add to Home Screen"), "."),
      h("button", { class: "m-hint-x", "aria-label": "Dismiss", onclick: () => this.dismissHint() }, "Not now"));
  }

  private dismissed(): boolean {
    try { return localStorage.getItem(HINT_KEY) === "1"; } catch { return this.hintShown; }
  }
  private hintShown = false;

  private maybeHint() {
    if (this.hintShown || !this.active) return;
    const standalone = (navigator as unknown as { standalone?: boolean }).standalone === true || matchMedia("(display-mode: standalone)").matches;
    if (!installHintDue({ iphone: isIphone(navigator.userAgent), standalone, dismissed: this.dismissed(), secondsOpen: (Date.now() - this.opened) / 1000 })) return;
    this.hintShown = true;
    this.hint.classList.remove("hidden");
  }

  private dismissHint() {
    this.hint.classList.add("hidden");
    this.hintShown = true;
    try { localStorage.setItem(HINT_KEY, "1"); } catch { /* storage unavailable: it just won't come back this visit */ }
  }
}
