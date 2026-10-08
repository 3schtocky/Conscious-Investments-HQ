import { STATIC } from "./static";
// Conscious Investments HQ: the office UI.
import Phaser from "phaser";
import "./style.css";
import "./mobile.css";
import "./mobile-screens.css";
import { OfficeScene, type Focus } from "./office/scene";
import { OfficeState, type OfficeEvent, type Snapshot } from "./state";
import { h, money } from "./ui/dom";
import { Overlay } from "./ui/overlay";
import { AgentPanel, ChatPanel, DeskCards, renderActivity, WING_ORDER } from "./ui/panels";
import { SettingsPanel } from "./ui/settings";
import { ApprovalsPanel } from "./ui/approvals";
import { BossChannel } from "./ui/boss";
import { WatchlistPanel } from "./ui/watchlist";
import { AuditPanel } from "./ui/audit";
import { RoundsPanel } from "./ui/rounds";
import { OutboxPanel } from "./ui/outbox";
import { PortfolioPanel, pct as signedPct } from "./ui/portfolio";
import { NewsPanel, SignInPanel } from "./ui/visitor";
import { Replayer } from "./ui/replay";
import { DemoTour } from "./ui/demoTour";
import { MobileShell } from "./ui/mobile";
import { AgentSheet } from "./ui/agentSheet";
import { Captions } from "./ui/captions";
import { MobileHome } from "./ui/mobileHome";
import { MobileDemo } from "./ui/mobileDemo";
import { MobileFeed } from "./ui/feedMobile";
import { isMobileLayout, opensOnHome } from "./ui/mobileModel";
import { AndroidBack } from "./ui/androidBack";
import { platformOf } from "./ui/androidModel";
import { watchInstall } from "./ui/install";

type Tab = "activity" | "agent" | "chat" | "approvals" | "rounds" | "watchlist" | "portfolio" | "audit" | "outbox" | "settings" | "news" | "signin" | "demo";
let TABS: Tab[] = ["activity", "agent", "chat", "approvals", "rounds", "watchlist", "portfolio", "audit", "outbox", "settings"];
// On the public site a visitor (anyone who hasn't signed in as the Captain) gets a read-only
// office: the floor, the team, the watchlist, the scoreboard and published newsletters.
const VISITOR_TABS: Tab[] = STATIC ? ["activity", "demo", "agent", "watchlist", "portfolio", "news"] : ["activity", "demo", "agent", "watchlist", "portfolio", "news", "signin"];
let visitor = false;
let publicSite = false;
// Events that change what the Rounds board shows (it re-reads cheap code on the server).
const ROUNDS_EVENTS = new Set(["rounds", "status", "task_started", "task_done", "task_paused", "approval_requested",
  "approval_decided", "incident", "office_hold", "office_status", "delegated"]);
const OUTBOX_EVENTS = new Set(["outbox_draft", "outbox_ready", "outbox_status"]);
// Events that change what the Audit tab shows.
const AUDIT_EVENTS = new Set(["audit_flag", "audit_review", "audit_resolved", "memory_saved", "memory_held",
  "memory_decided", "audit_digest", "status", "incident", "task_done"]);

const state = new OfficeState();
let focus: Focus = { kind: "floor" };
let tab: Tab = "activity";
let scene: OfficeScene | null = null;

// ---- layout ---------------------------------------------------------------------------------
const spendFill = h("div", { class: "meter-fill" });
const spendText = h("span", { class: "meter-text" });
const statusPill = h("span", { class: "pill office-status" });
const demoBtn = h("button", { class: "demo-btn hidden", title: "Scripted demo: watch the whole office make a Micron report, model and client note in five minutes. No API calls, no real spend.",
  onclick: () => void demoTour.start() }, "▶ Demo");
const crumbs = h("div", { class: "crumbs" });
const awaiting = h("button", { class: "pill awaiting hidden", onclick: () => { tab = "approvals"; render(); } });
const signOut = h("button", { class: "pill signout hidden", title: "Sign out of the Captain's office", onclick: async () => {
  await fetch("/api/logout", { method: "POST" });
  location.reload();
} }, "Sign out");
const scorePill = h("button", { class: "pill score hidden", title: "Paper portfolio vs the S&P 500 since the first position",
  onclick: () => { tab = "portfolio"; render(); } });
// Theme: follows the system unless the Captain picks one (remembered per browser).
const THEME_KEY = "hq-theme";
function applyTheme(theme: string | null) {
  if (theme) document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme;
}
try { applyTheme(localStorage.getItem(THEME_KEY)); } catch { /* storage unavailable */ }
const themeBtn = h("button", { class: "icon-btn", title: "Switch light / dark", onclick: () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
} }, "◐");
// Pause the whole office: nobody takes another step, so there is time to read models, reports
// and concerns, and to talk to anyone one to one. Resume picks every task up where it stopped.
const holdBtn = h("button", { class: "hold-btn", onclick: async () => {
  holdBtn.disabled = true;
  await fetch(state.held ? "/api/office/release" : "/api/office/hold", { method: "POST" }).catch(() => null);
  holdBtn.disabled = false;
} });
const contactBtn = h("a", { class: "contact-btn", href: "mailto:stott@consciousinvestments.org", title: "Email stott@consciousinvestments.org" }, "Contact");
const header = h("header", {},
  h("div", { class: "brand" }, h("span", { class: "brand-mark" }), h("span", {}, "Conscious Investments ", h("b", {}, "HQ"))),
  statusPill, demoBtn, awaiting, scorePill, crumbs,
  h("div", { class: "meter", title: "Today's API spend vs. the daily cap" }, h("div", { class: "meter-bar" }, spendFill), spendText),
  holdBtn,
  signOut, themeBtn, contactBtn);

const stage = h("div", { class: "stage" });
const overlayLayer = h("div", { class: "overlay" });
const viewButtons = h("div", { class: "views" });
const cardsRoot = h("div", { class: "cards" });
// A mouse wheel (or a trackpad swipe up and down) over the agent cards scrolls them sideways.
cardsRoot.addEventListener("wheel", (e) => {
  const canScroll = cardsRoot.scrollWidth > cardsRoot.clientWidth;
  if (!canScroll || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;   // sideways swipes already work
  const px = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;              // lines -> pixels
  const max = cardsRoot.scrollWidth - cardsRoot.clientWidth;
  const next = Math.min(max, Math.max(0, cardsRoot.scrollLeft + px));
  if (next === cardsRoot.scrollLeft) return;                           // at an end: let the page scroll
  e.preventDefault();
  cardsRoot.scrollLeft = next;
}, { passive: false });
const tabsBar = h("nav", { class: "tabs" });
const panelRoot = h("div", { class: "panel" });
const bossBar = h("div", { class: "boss" });
// Visitors only: plays back recent work when the floor is quiet (see ui/replay.ts).
const replay = new Replayer(state, () => scene, () => loadState().catch(() => {}));
// Visitors only: the five-minute Micron demo, played from a recorded script in their own browser (ui/demoTour.ts).
const demoTour = new DemoTour(state, () => scene, () => loadState().catch(() => {}), () => render(), () => { tab = "demo"; render(); },
  () => { tab = "portfolio"; render(); });
// Shown when the live connection drops (the office restarting, a flaky phone connection).
const linkBanner = h("div", { class: "replay-banner link hidden", role: "status" }, "Reconnecting to the office…");

// The sidebar's left edge is a handle: drag it (or focus it and use the arrow keys) to trade
// office floor for reading room. Double-click resets. The width is remembered per browser.
const SIDE_KEY = "hq-sidebar-width", SIDE_DEFAULT = 400, SIDE_MIN = 400, FLOOR_MIN = 380;   // 400 keeps all nine tabs visible
const resizer = h("div", { class: "resizer", role: "separator", tabindex: 0, "aria-orientation": "vertical",
  "aria-label": "Resize the sidebar", title: "Drag to resize the sidebar (double-click to reset)" });
const aside = h("aside", {}, resizer, tabsBar, panelRoot, bossBar);
const stageWrap = h("div", { class: "stage-wrap" }, stage, overlayLayer, viewButtons, replay.banner, linkBanner);
const mainEl = h("main", {},
  h("section", { class: "floor" }, stageWrap, cardsRoot),
  aside);
document.getElementById("app")!.append(header, demoTour.bar, mainEl);
// The iPhone layout (ui/mobile.ts): a masthead and bottom tab bar around the same panels and map,
// with a caption strip, an agent sheet and a quiet-state summary. It switches on in boot() once we
// know the visitor is not the Captain.
const captions = new Captions(state);
const agentSheet = new AgentSheet(state);
const mobileHome = new MobileHome({
  state,
  portfolio: () => portfolio.view,
  latestNote: () => { const i = news.items[0]; return i ? { title: i.title, date: i.date, words: i.words } : null; },
  lastActive: () => (state.feed.length ? state.feed[state.feed.length - 1].ts : null),
  watch: () => { mobile.showMap(); setFocus({ kind: "floor" }); replay.play(); },
  openFloor: () => mobile.showMap(),
  openPortfolio: () => mobile.go("portfolio"),
  openNote: () => mobile.go("more", "news"),
  openDemo: () => mobileDemo.start(),
});
// The Micron demo on a phone: a docked player, a camera that follows the action, a toast when a
// file is ready, and a one-time invitation (ui/mobileDemo.ts).
const mobileDemo = new MobileDemo({
  tour: demoTour, state, captions,
  view: { current: () => (focus.kind === "wing" ? focus.id : "floor"), set: (id) => setFocus(id === "floor" ? { kind: "floor" } : { kind: "wing", id }) },
  goFloor: () => { mobile.go("floor"); mobile.showMap(); },
  openFile: (kind) => { mobile.go("more", "demo"); demoTour.panel.show(kind); },
  onFloor: () => mobile.active && mobile.tab === "floor" && mobile.fmode === "map",
});
demoTour.panel.mobileOps = {
  startDemo: () => mobileDemo.start(),
  backToFloor: () => { mobile.go("floor"); mobile.showMap(); },
  status: () => { const v = demoTour.view(); return { playing: !!v && v.active && !v.finished, finished: !!v?.finished, started: !!v }; },
};
const mobile = new MobileShell(document.getElementById("app")!, mainEl, {
  statusPill, desktopHeader: header,
  showPanel: (t) => { tab = t; render(); },
  toggleTheme: () => themeBtn.click(),
  themeLabel: () => { const t = document.documentElement.dataset.theme; return t === "dark" ? "Dark" : t === "light" ? "Light" : "Automatic"; },
  contactHref: contactBtn.getAttribute("href") ?? "",
  stageWrap, chips: viewButtons, chipsHome: overlayLayer, captions, sheet: agentSheet, home: mobileHome,
  view: { current: () => (focus.kind === "wing" ? focus.id : "floor"), set: (id) => { mobileDemo.userMoved(); setFocus(id === "floor" ? { kind: "floor" } : { kind: "wing", id }); } },
  demo: mobileDemo,
  demoPill: () => { if (state.touring) { mobile.go("floor"); mobile.showMap(); } else mobileDemo.start(); },
  onLeaveFloor: () => demoTour.pauseIfPlaying(),
  files: { title: () => demoTour.panel.pageTitle, back: () => demoTour.panel.back(), onList: () => demoTour.panel.onList },
  // The map only needs drawing while it is on screen: a phone should not animate an office nobody is looking at.
  onFloorVisible: (visible) => { if (visible) { game?.loop.wake(); fitCanvas(); } else game?.loop.sleep(); },
});

// Android: the system Back button walks back through the app instead of leaving it (ui/androidBack.ts).
watchInstall();   // catches the browser's install offer early, for our own Install button
const androidBack = platformOf(navigator.userAgent) === "android"
  ? new AndroidBack({ depth: () => mobile.backDepth, stepBack: () => mobile.stepBack() }) : null;

let game: Phaser.Game | null = null;
/** Keep the office canvas exactly the size of its container. Phaser only re-measures when the
 *  window resizes, but the container also changes when the desk cards load or the sidebar is
 *  dragged; a canvas left taller than its container puts the floor off-centre and cut off. */
function fitCanvas() {
  if (!game || !stage.clientWidth || !stage.clientHeight) return;   // a zero-size canvas breaks WebGL
  game.scale.getParentBounds();
  game.scale.refresh();
}
new ResizeObserver(() => fitCanvas()).observe(stage);
let sideWidth = SIDE_DEFAULT;
function setSideWidth(px: number, save = true) {
  const max = Math.max(SIDE_MIN, window.innerWidth - FLOOR_MIN);
  sideWidth = Math.round(Math.min(Math.max(px, SIDE_MIN), max));
  mainEl.style.setProperty("--side", `${sideWidth}px`);
  aside.classList.toggle("wide", sideWidth >= 500);
  resizer.setAttribute("aria-valuenow", String(sideWidth));
  fitCanvas();
  if (save) { try { localStorage.setItem(SIDE_KEY, String(sideWidth)); } catch { /* storage unavailable */ } }
}
try { const saved = Number(localStorage.getItem(SIDE_KEY)); if (saved) sideWidth = saved; } catch { /* storage unavailable */ }
setSideWidth(sideWidth, false);
resizer.addEventListener("pointerdown", (e: PointerEvent) => {
  e.preventDefault();
  resizer.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing");
  const move = (ev: PointerEvent) => setSideWidth(window.innerWidth - ev.clientX, false);
  const up = () => {
    resizer.removeEventListener("pointermove", move);
    document.body.classList.remove("resizing");
    setSideWidth(sideWidth);
  };
  resizer.addEventListener("pointermove", move);
  resizer.addEventListener("pointerup", up, { once: true });
  resizer.addEventListener("pointercancel", up, { once: true });
});
resizer.addEventListener("dblclick", () => setSideWidth(SIDE_DEFAULT));
resizer.addEventListener("keydown", (e: KeyboardEvent) => {
  const step = e.shiftKey ? 80 : 24;
  if (e.key === "ArrowLeft") { e.preventDefault(); setSideWidth(sideWidth + step); }
  if (e.key === "ArrowRight") { e.preventDefault(); setSideWidth(sideWidth - step); }
  if (e.key === "Home") { e.preventDefault(); setSideWidth(SIDE_DEFAULT); }
});
window.addEventListener("resize", () => setSideWidth(sideWidth, false));   // keep the floor's minimum

// ---- panels ---------------------------------------------------------------------------------
const actRoot = h("div", { class: "feed" });
const agentRoot = h("div", { class: "agent-view" });
const chatRoot = h("div", { class: "chat" });
const settingsRoot = h("div", { class: "settings" });
const api = async (path: string, init?: RequestInit) => fetch(path, init);
const agentPanel = new AgentPanel(agentRoot, state, {
  pause: async (id) => { await api(`/api/agents/${id}/pause`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "Paused by the Captain" }) }); },
  resume: async (id) => { await api(`/api/agents/${id}/resume`, { method: "POST" }); },
  chat: (id) => startChat(id),
});
const chatPanel = new ChatPanel(chatRoot, state);
const settings = new SettingsPanel(settingsRoot, state);
const approvalsRoot = h("div", { class: "approvals" });
const approvals = new ApprovalsPanel(approvalsRoot, state, () => render());
const boss = new BossChannel(bossBar, state);
const watchRoot = h("div", { class: "watchlist" });
const watchlist = new WatchlistPanel(watchRoot, state, () => render());
const roundsRoot = h("div", { class: "audit rounds-panel" });
const rounds = new RoundsPanel(roundsRoot, state, () => render());
const auditRoot = h("div", { class: "audit" });
const audit = new AuditPanel(auditRoot, state, () => render());
const portfolioRoot = h("div", { class: "audit portfolio-panel" });
const portfolio = new PortfolioPanel(portfolioRoot, state, () => render(), () => { tab = "approvals"; render(); });
portfolio.extra = () => demoTour.panel.positionCard();   // the recorded demo's own position card
const newsRoot = h("div", { class: "audit" });
const news = new NewsPanel(newsRoot, () => render());
const signinRoot = h("div", { class: "audit" });
const signin = new SignInPanel(signinRoot, () => state.captain.nickname, () => render());
const outboxRoot = h("div", { class: "audit outbox-panel" });
const outbox = new OutboxPanel(outboxRoot, state, () => render(), () => { tab = "approvals"; render(); });
// Picking a thread addresses the chat bar to it (never over a draft in progress).
chatPanel.onChannel = (to) => { if (to && tab === "chat") boss.setDefaultRecipient(to); };

/** Open the Captain's 1-1 thread with an agent and address the chat bar to them. */
function startChat(id: string) {
  chatPanel.open(`dm:captain|${id}`);
  tab = "chat";
  boss.setRecipient(id, true);
  render();
}
const cards = new DeskCards(cardsRoot, state, (id) => pickAgent(id));
const mobileFeed = new MobileFeed(actRoot, state, (id) => pickAgent(id));   // the phone's work log (ui/feedMobile.ts)

function pickAgent(id: string) {
  if (mobile.active && state.agents.has(id)) { mobile.openAgent(id); return; }   // a phone gets a sheet, not the sidebar
  if (!state.agents.has(id)) { if (id === "captain" && !visitor) { tab = "settings"; settings.select("captain"); render(); } return; }
  setFocus({ kind: "agent", id });
  tab = "agent";
  render();
}

function setFocus(f: Focus) {
  focus = f;
  mobile.setViewKind(f.kind === "floor" ? "floor" : "wing");
  if (mobile.active && mobile.fmode === "home") mobile.showMap();
  boss.setDefaultRecipient(f.kind === "agent" ? f.id : null);
  scene?.setFocus(f);
  writeHash();
  render();
  requestAnimationFrame(() => {
    if (f.kind === "agent") cards.reveal([f.id]);
    if (f.kind === "wing") cards.reveal([...state.agents.values()].filter((a) => a.wing === f.id).map((a) => a.id));
  });
}

// Deep links: #wing=screening, #agent=er_lead&tab=chat, #tab=settings
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const t = p.get("tab");
  if (t && (TABS as string[]).includes(t)) tab = t as Tab;
  const agent = p.get("agent"), wing = p.get("wing");
  if (agent && state.agents.has(agent)) {
    focus = { kind: "agent", id: agent };
    if (!t) tab = "agent";
    if (tab === "settings") settings.select(agent);
  }
  else if (wing && state.wings[wing]) focus = { kind: "wing", id: wing };
}

function writeHash() {
  if (mobile.active) return;   // a phone keeps its place in the shell, not in the address; rewriting it would also disturb the Back history
  const p = new URLSearchParams();
  if (focus.kind === "agent") p.set("agent", focus.id);
  if (focus.kind === "wing") p.set("wing", focus.id);
  if (tab !== "activity") p.set("tab", tab);
  history.replaceState(null, "", p.toString() ? `#${p}` : location.pathname);
}

function renderTabs() {
  tabsBar.replaceChildren(...TABS.map((t) =>
    h("button", { class: `tab${t === tab ? " active" : ""}${t === "settings" ? " gear" : ""}`, title: t === "settings" ? "Settings" : undefined, onclick: () => {
      if (t === "settings" && focus.kind === "agent") settings.select(focus.id);
      tab = t; render();
    } },
      { activity: "Feed", agent: "Agent", chat: "Chat", approvals: "Approvals", rounds: "Rounds", watchlist: "Watchlist", portfolio: "Portfolio", audit: "Audit", outbox: "Outbox", settings: "⚙", demo: "Demo", news: "Newsletter", signin: "Sign in" }[t].replace(/^Agent$/, visitor ? "Team" : "Agent"),
      t === "demo" && demoTour.panel.count ? h("span", { class: "badge" }, String(demoTour.panel.count)) : null,
      t === "outbox" && outbox.count ? h("span", { class: "badge quiet" }, String(outbox.count)) : null,
      t === "rounds" && rounds.count ? h("span", { class: "badge" }, String(rounds.count)) : null,
      t === "audit" && audit.count ? h("span", { class: "badge" }, String(audit.count)) : null,
      !visitor && t === "approvals" && approvals.pending ? h("span", { class: "badge" }, String(approvals.pending)) : null,
      !visitor && t === "watchlist" && watchlist.count ? h("span", { class: "badge quiet" }, String(watchlist.count)) : null)));
  writeHash();
  // Swap the panel only on a real tab change: re-attaching an element resets its scroll.
  const panel = { activity: actRoot, agent: agentRoot, chat: chatRoot, approvals: approvalsRoot, rounds: roundsRoot, watchlist: watchRoot, portfolio: portfolioRoot, audit: auditRoot, outbox: outboxRoot, demo: demoTour.panel.root, settings: settingsRoot, news: newsRoot, signin: signinRoot }[tab];
  if (panelRoot.firstElementChild !== panel) panelRoot.replaceChildren(panel);
}

function renderViews() {
  const wings = WING_ORDER.filter((w) => w !== "executive");
  const btn = (label: string, f: Focus, active: boolean) =>
    h("button", { class: `view${active ? " active" : ""}`, onclick: () => { mobileDemo.userMoved(); setFocus(f); } }, label);
  const phone = mobile.active;
  viewButtons.replaceChildren(
    ...(phone ? [h("button", { class: "view summary", onclick: () => mobile.showHome() }, "Summary")] : []),
    btn("Whole floor", { kind: "floor" }, focus.kind === "floor"),
    ...wings.map((w) => btn(state.wings[w] ?? w, { kind: "wing", id: w }, focus.kind === "wing" && focus.id === w)),
    ...(phone ? [btn("Lobby", { kind: "wing", id: "lobby" }, focus.kind === "wing" && focus.id === "lobby")] : []));
  if (phone) {   // keep the chosen chip in view as swipes move along the row
    const active = viewButtons.querySelector<HTMLElement>(".view.active");
    if (active) viewButtons.scrollTo({ left: Math.max(0, active.offsetLeft - 16), behavior: "smooth" });
  }
  const parts = ["Floor"];
  if (focus.kind === "wing") parts.push(state.wings[focus.id] ?? focus.id);
  if (focus.kind === "agent") {
    const a = state.agents.get(focus.id);
    if (a) parts.push(state.wings[a.wing] ?? a.wing, a.nickname);
  }
  crumbs.textContent = parts.join(" › ");
}

function renderHeader() {
  if (visitor) {   // no spend, no desk: just whether the office is open
    const working = [...state.agents.values()].filter((a) => a.status === "working").length;
    statusPill.textContent = state.replaying ? "↻ Replay" : state.held ? "⏸ Paused" : state.clockedOut ? "🌙 Clocked out" : working ? `● ${working} working` : "● Open";
    statusPill.dataset.state = state.replaying ? "open" : state.held || state.clockedOut ? "closed" : working ? "busy" : "open";
    const pv = portfolio.view;
    const scored = !!pv && pv.return !== null && pv.priced;
    scorePill.classList.toggle("hidden", !scored);
    if (pv && scored) {
      scorePill.textContent = `Portfolio ${signedPct(pv.return)} · S&P ${signedPct(pv.benchmark_return)}`;
      scorePill.dataset.state = pv.vs_benchmark !== null && pv.vs_benchmark >= 0 ? "ahead" : "behind";
    }
    return;
  }
  const pct = Math.min(100, (state.spend.today / Math.max(state.spend.cap, 0.01)) * 100);
  spendFill.style.width = `${pct}%`;
  spendFill.dataset.level = pct > 90 ? "high" : pct > 60 ? "mid" : "low";
  spendText.textContent = `${money(state.spend.today)} of $${state.spend.cap.toFixed(2)} today`;
  const working = [...state.agents.values()].filter((a) => a.status === "working").length;
  statusPill.textContent = state.held ? "⏸ Paused" : state.clockedOut ? "🌙 Clocked out" : working ? `● ${working} working` : "● Open";
  statusPill.dataset.state = state.held || state.clockedOut ? "closed" : working ? "busy" : "open";
  holdBtn.textContent = state.held ? "▶ Resume office" : "⏸ Pause office";
  holdBtn.title = state.held
    ? "Resume: every task picks up where it stopped"
    : "Pause the whole office: steps in progress finish, then everyone waits. You can still message anyone one to one.";
  holdBtn.classList.toggle("held", state.held);
  document.body.classList.toggle("office-held", state.held);
  const n = approvals.pending;
  awaiting.textContent = `${n} awaiting you`;
  awaiting.classList.toggle("hidden", n === 0);
  const pv = portfolio.view;
  const scored = !!pv && pv.return !== null && pv.priced;
  scorePill.classList.toggle("hidden", !scored);
  if (pv && scored) {
    scorePill.textContent = `Portfolio ${signedPct(pv.return)} · S&P ${signedPct(pv.benchmark_return)}`;
    scorePill.dataset.state = pv.vs_benchmark !== null && pv.vs_benchmark >= 0 ? "ahead" : "behind";
  }
}

let dirty = true;
let structural = true;
function render() { dirty = true; structural = true; }
function frame() {
  if (mobile.active) { mobile.syncTitle(); androidBack?.sync(); }
  if (dirty) {
    dirty = false;
    renderHeader();
    const selected = focus.kind === "agent" ? focus.id : null;
    const wing = focus.kind === "wing" ? focus.id : null;
    cards.render(selected, wing);
    if (mobile.active && mobile.fmode === "home" && mobile.tab === "floor") mobileHome.render();
    if (tab === "agent") agentPanel.render(selected);
    if (structural) {
      structural = false;
      renderTabs();
      renderViews();
      if (tab === "activity") { if (mobile.active) mobileFeed.render(); else { actRoot.classList.remove("mf"); renderActivity(actRoot, state, wing, pickAgent); } }
      if (tab === "chat") chatPanel.render(selected);
      if (tab === "demo") demoTour.panel.render();
      if (tab === "settings") settings.render();
      if (tab === "approvals") approvals.render();
      if (tab === "rounds") { rounds.render(); rounds.maybeRefresh(); }
      if (tab === "watchlist") { watchlist.render(); watchlist.maybeRefresh(); }
      if (tab === "audit") { audit.render(); audit.maybeRefresh(); }
      if (tab === "outbox") outbox.render();
      if (tab === "portfolio") { portfolio.render(); portfolio.maybeRefresh(); }
      if (tab === "news") news.render();
      if (tab === "signin") signin.render();
    }
  }
  requestAnimationFrame(frame);
}

// ---- data -----------------------------------------------------------------------------------
async function loadState() {
  const snap: Snapshot = await (await fetch(visitor ? "/api/public/state" : "/api/state")).json();
  state.load(snap);
  approvals.items = (snap as any).approvals ?? [];
  watchlist.refresh();
  portfolio.refresh();
  if (visitor) news.refresh();
  else { audit.refresh(); outbox.refresh(); rounds.refresh(); }
  scene?.sync();
  render();
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (m) => {
    const ev: OfficeEvent = JSON.parse(m.data);
    if (ev.type === "ping") return;   // keep-alive only
    if (ev.type === "roster_updated" && !visitor) { loadState(); }
    if (visitor && state.touring) return;   // the recorded demo owns the floor until it is closed
    if (visitor) {   // a visitor's stream carries movement and nudges only; refetch the public views
      if (ev.type === "roster_updated") loadState();
      if (ev.type === "watchlist_added" || ev.type === "watchlist_status") watchlist.refresh();
      if (ev.type === "portfolio_changed") portfolio.refresh();
      if (ev.type === "outbox_status") news.refresh();
      replay.live(ev);
      state.apply(ev); scene?.handle(ev); dirty = true;
      return;
    }
    if (["approval_requested", "approval_decided", "audit_resolved", "audit_review"].includes(ev.type)) { approvals.refresh(); }
    if (ev.type === "watchlist_added" || ev.type === "watchlist_status") { watchlist.refresh(); }
    if (OUTBOX_EVENTS.has(ev.type)) { outbox.refresh(); }
    if (ROUNDS_EVENTS.has(ev.type) && (tab === "rounds" || ev.type === "rounds")) { rounds.refresh(); }
    if (ev.type === "portfolio_changed" || (["approval_requested", "approval_decided"].includes(ev.type) && ev.kind === "portfolio")) { portfolio.refresh(); }
    if (AUDIT_EVENTS.has(ev.type) && (tab === "audit" || !["status", "task_done"].includes(ev.type))) { audit.refresh(); }
    if (tab === "agent" && focus.kind === "agent" && ev.agent === focus.id &&
        ["approval_requested", "approval_decided", "captain_report", "task_done"].includes(ev.type)) {
      agentPanel.loadDocs(focus.id);
    }
    state.apply(ev);
    scene?.handle(ev);
    dirty = true;
    if (!ev.type.endsWith("_delta")) {
      structural = structural || tab === "activity" || tab === "chat";
    }
  };
  ws.onopen = () => linkBanner.classList.add("hidden");
  ws.onclose = () => {
    linkBanner.classList.remove("hidden");
    setTimeout(async () => { await loadState().catch(() => {}); connect(); }, 1500);
  };
}

state.on(() => { dirty = true; });

async function boot() {
  const session = await (await fetch("/api/session")).json().catch(() => ({ role: "captain", public: false }));
  publicSite = !!session.public;
  visitor = session.role === "visitor";
  if (visitor) {
    TABS = VISITOR_TABS;
    tab = "activity";   // the live feed is the first thing a visitor sees beside the floor
    document.body.classList.add("visitor");
    demoBtn.classList.remove("hidden");
    watchlist.readOnly = portfolio.readOnly = agentPanel.readOnly = true;
  }
  signOut.classList.toggle("hidden", !publicSite || visitor);
  const forceMobile = new URLSearchParams(location.search).has("mobile");   // ?mobile=1: try the phone layout on a desktop
  const fitLayout = () => {
    mobile.setActive(isMobileLayout({ width: window.innerWidth, visitor, forced: forceMobile }));
    mobile.setViewKind(focus.kind === "floor" ? "floor" : "wing");
    render();   // the chip row differs on a phone
  };
  fitLayout();
  window.addEventListener("resize", fitLayout);
  await loadState();
  if (visitor) {   // a new visitor lands on recent activity, not an empty list
    const recent = await fetch("/api/public/replay").then((r) => (r.ok ? r.json() : { events: [] })).catch(() => ({ events: [] }));
    state.seedFeed(recent.events ?? []);
    render();
  }
  readHash();
  mobile.setViewKind(focus.kind === "floor" ? "floor" : "wing");
  // A phone opens on the summary when the office is quiet, and on the floor when someone is working.
  mobile.settle(opensOnHome({ working: [...state.agents.values()].filter((a) => a.status === "working").length,
    replaying: state.replaying, touring: state.touring }));
  const overlay = new Overlay(overlayLayer, state, () => scene);
  game = new Phaser.Game({
    type: Phaser.AUTO,
    parent: stage,
    pixelArt: true,
    roundPixels: true,
    backgroundColor: "#d8d3c7",
    scale: { mode: Phaser.Scale.RESIZE, width: "100%", height: "100%" },
    scene: [],
    callbacks: {
      postBoot: (game) => {
        game.scene.add("office", OfficeScene, true, {
          state,
          focus,
          hooks: {
            onAgentClick: (id: string) => pickAgent(id),
            onDoubleTap: (room: string | null) => {
              if (focus.kind !== "floor") setFocus({ kind: "floor" });
              else if (room && WING_ORDER.includes(room) && room !== "executive") setFocus({ kind: "wing", id: room });
              else if (room === "captain") pickAgent("captain");
            },
            say: (id: string, text: string, ms: number) => overlay.say(id, text, ms),
          },
        });
        scene = game.scene.getScene("office") as OfficeScene;
      },
    },
  });
  boss.render();
  // A deep link opened in an already-open page (bookmark, pasted URL) re-applies the view.
  window.addEventListener("hashchange", () => { readHash(); scene?.setFocus(focus); render(); });
  connect();
  requestAnimationFrame(frame);
}

if (import.meta.env.DEV) Object.assign(window, { __demoTour: demoTour, __mobile: mobile });   // for browser checks

boot().catch((e) => {
  document.getElementById("app")!.replaceChildren(h("div", { class: "boot-error" },
    h("h2", {}, "Can't reach the office server."), h("p", {}, "Start it with ", h("code", {}, "uv run hq serve --demo"), " and reload."),
    h("pre", {}, String(e))));
});
