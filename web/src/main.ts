import { STATIC } from "./static";
// Conscious Investments HQ: the office UI.
import Phaser from "phaser";
import "./style.css";
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
import { OutboxPanel } from "./ui/outbox";
import { PortfolioPanel, pct as signedPct } from "./ui/portfolio";
import { NewsPanel, SignInPanel } from "./ui/visitor";
import { Replayer } from "./ui/replay";

type Tab = "activity" | "agent" | "chat" | "approvals" | "watchlist" | "portfolio" | "audit" | "outbox" | "settings" | "news" | "signin";
let TABS: Tab[] = ["activity", "agent", "chat", "approvals", "watchlist", "portfolio", "audit", "outbox", "settings"];
// On the public site a visitor (anyone who hasn't signed in as the Captain) gets a read-only
// office: the floor, the team, the watchlist, the scoreboard and published newsletters.
const VISITOR_TABS: Tab[] = STATIC ? ["agent", "watchlist", "portfolio", "news"] : ["agent", "watchlist", "portfolio", "news", "signin"];
let visitor = false;
let publicSite = false;
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
const demoBadge = h("span", { class: "pill demo hidden", title: "Scripted demo office: no API calls, no real spend" }, "DEMO");
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
const header = h("header", {},
  h("div", { class: "brand" }, h("span", { class: "brand-mark" }), h("span", {}, "Conscious Investments ", h("b", {}, "HQ"))),
  statusPill, demoBadge, awaiting, scorePill, crumbs,
  h("div", { class: "meter", title: "Today's API spend vs. the daily cap" }, h("div", { class: "meter-bar" }, spendFill), spendText),
  holdBtn,
  signOut, themeBtn);

const stage = h("div", { class: "stage" });
const overlayLayer = h("div", { class: "overlay" });
const viewButtons = h("div", { class: "views" });
const cardsRoot = h("div", { class: "cards" });
const tabsBar = h("nav", { class: "tabs" });
const panelRoot = h("div", { class: "panel" });
const bossBar = h("div", { class: "boss" });
// Visitors only: plays back recent work when the floor is quiet (see ui/replay.ts).
const replay = new Replayer(state, () => scene, () => loadState().catch(() => {}));
// Shown when the live connection drops (the office restarting, a flaky phone connection).
const linkBanner = h("div", { class: "replay-banner link hidden", role: "status" }, "Reconnecting to the office…");

// The sidebar's left edge is a handle: drag it (or focus it and use the arrow keys) to trade
// office floor for reading room. Double-click resets. The width is remembered per browser.
const SIDE_KEY = "hq-sidebar-width", SIDE_DEFAULT = 400, SIDE_MIN = 400, FLOOR_MIN = 380;   // 400 keeps all nine tabs visible
const resizer = h("div", { class: "resizer", role: "separator", tabindex: 0, "aria-orientation": "vertical",
  "aria-label": "Resize the sidebar", title: "Drag to resize the sidebar (double-click to reset)" });
const aside = h("aside", {}, resizer, tabsBar, panelRoot, bossBar);
const mainEl = h("main", {},
  h("section", { class: "floor" }, h("div", { class: "stage-wrap" }, stage, overlayLayer, viewButtons, replay.banner, linkBanner), cardsRoot),
  aside);
document.getElementById("app")!.append(header, mainEl);

let game: Phaser.Game | null = null;
/** Keep the office canvas exactly the size of its container. Phaser only re-measures when the
 *  window resizes, but the container also changes when the desk cards load or the sidebar is
 *  dragged; a canvas left taller than its container puts the floor off-centre and cut off. */
function fitCanvas() {
  if (!game) return;
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
const auditRoot = h("div", { class: "audit" });
const audit = new AuditPanel(auditRoot, state, () => render());
const portfolioRoot = h("div", { class: "audit portfolio-panel" });
const portfolio = new PortfolioPanel(portfolioRoot, state, () => render(), () => { tab = "approvals"; render(); });
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

function pickAgent(id: string) {
  if (!state.agents.has(id)) { if (id === "captain" && !visitor) { tab = "settings"; settings.select("captain"); render(); } return; }
  setFocus({ kind: "agent", id });
  tab = "agent";
  render();
}

function setFocus(f: Focus) {
  focus = f;
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
      { activity: "Feed", agent: "Agent", chat: "Chat", approvals: "Approvals", watchlist: "Watchlist", portfolio: "Portfolio", audit: "Audit", outbox: "Outbox", settings: "⚙", news: "Newsletter", signin: "Sign in" }[t].replace(/^Agent$/, visitor ? "Team" : "Agent"),
      t === "outbox" && outbox.count ? h("span", { class: "badge quiet" }, String(outbox.count)) : null,
      t === "audit" && audit.count ? h("span", { class: "badge" }, String(audit.count)) : null,
      !visitor && t === "approvals" && approvals.pending ? h("span", { class: "badge" }, String(approvals.pending)) : null,
      !visitor && t === "watchlist" && watchlist.count ? h("span", { class: "badge quiet" }, String(watchlist.count)) : null)));
  writeHash();
  // Swap the panel only on a real tab change: re-attaching an element resets its scroll.
  const panel = { activity: actRoot, agent: agentRoot, chat: chatRoot, approvals: approvalsRoot, watchlist: watchRoot, portfolio: portfolioRoot, audit: auditRoot, outbox: outboxRoot, settings: settingsRoot, news: newsRoot, signin: signinRoot }[tab];
  if (panelRoot.firstElementChild !== panel) panelRoot.replaceChildren(panel);
}

function renderViews() {
  const wings = WING_ORDER.filter((w) => w !== "executive");
  const btn = (label: string, f: Focus, active: boolean) =>
    h("button", { class: `view${active ? " active" : ""}`, onclick: () => setFocus(f) }, label);
  viewButtons.replaceChildren(
    btn("Whole floor", { kind: "floor" }, focus.kind === "floor"),
    ...wings.map((w) => btn(state.wings[w] ?? w, { kind: "wing", id: w }, focus.kind === "wing" && focus.id === w)));
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
    demoBadge.classList.toggle("hidden", !state.demo);
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
  demoBadge.classList.toggle("hidden", !state.demo);
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
  if (dirty) {
    dirty = false;
    renderHeader();
    const selected = focus.kind === "agent" ? focus.id : null;
    const wing = focus.kind === "wing" ? focus.id : null;
    cards.render(selected, wing);
    if (tab === "agent") agentPanel.render(selected);
    if (structural) {
      structural = false;
      renderTabs();
      renderViews();
      if (tab === "activity") renderActivity(actRoot, state, wing, pickAgent);
      if (tab === "chat") chatPanel.render(selected);
      if (tab === "settings") settings.render();
      if (tab === "approvals") approvals.render();
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
  else { audit.refresh(); outbox.refresh(); }
  scene?.sync();
  render();
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (m) => {
    const ev: OfficeEvent = JSON.parse(m.data);
    if (ev.type === "ping") return;   // keep-alive only
    if (ev.type === "roster_updated" && !visitor) { loadState(); }
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
    tab = "agent";
    document.body.classList.add("visitor");
    watchlist.readOnly = portfolio.readOnly = agentPanel.readOnly = true;
  }
  signOut.classList.toggle("hidden", !publicSite || visitor);
  await loadState();
  readHash();
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
            onRoomClick: (room: string) => {
              if (WING_ORDER.includes(room) && room !== "executive") setFocus({ kind: "wing", id: room });
              else if (room === "captain") pickAgent("captain");
              else setFocus({ kind: "floor" });
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

boot().catch((e) => {
  document.getElementById("app")!.replaceChildren(h("div", { class: "boot-error" },
    h("h2", {}, "Can't reach the office server."), h("p", {}, "Start it with ", h("code", {}, "uv run hq serve --demo"), " and reload."),
    h("pre", {}, String(e))));
});
