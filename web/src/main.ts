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

type Tab = "activity" | "agent" | "chat" | "approvals" | "settings";
const TABS: Tab[] = ["activity", "agent", "chat", "approvals", "settings"];

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
const header = h("header", {},
  h("div", { class: "brand" }, h("span", { class: "brand-mark" }), h("span", {}, "Conscious Investments ", h("b", {}, "HQ"))),
  statusPill, demoBadge, awaiting, crumbs,
  h("div", { class: "meter", title: "Today's API spend vs. the daily cap" }, h("div", { class: "meter-bar" }, spendFill), spendText),
  themeBtn);

const stage = h("div", { class: "stage" });
const overlayLayer = h("div", { class: "overlay" });
const viewButtons = h("div", { class: "views" });
const cardsRoot = h("div", { class: "cards" });
const tabsBar = h("nav", { class: "tabs" });
const panelRoot = h("div", { class: "panel" });
const bossBar = h("div", { class: "boss" });

document.getElementById("app")!.append(header,
  h("main", {},
    h("section", { class: "floor" }, h("div", { class: "stage-wrap" }, stage, overlayLayer, viewButtons), cardsRoot),
    h("aside", {}, tabsBar, panelRoot, bossBar)));

// ---- panels ---------------------------------------------------------------------------------
const actRoot = h("div", { class: "feed" });
const agentRoot = h("div", { class: "agent-view" });
const chatRoot = h("div", { class: "chat" });
const settingsRoot = h("div", { class: "settings" });
const api = async (path: string, init?: RequestInit) => fetch(path, init);
const agentPanel = new AgentPanel(agentRoot, state, {
  pause: async (id) => { await api(`/api/agents/${id}/pause`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "Paused by the Captain" }) }); },
  resume: async (id) => { await api(`/api/agents/${id}/resume`, { method: "POST" }); },
});
const chatPanel = new ChatPanel(chatRoot, state);
const settings = new SettingsPanel(settingsRoot, state);
const approvalsRoot = h("div", { class: "approvals" });
const approvals = new ApprovalsPanel(approvalsRoot, state, () => render());
const boss = new BossChannel(bossBar, state);
const cards = new DeskCards(cardsRoot, state, (id) => pickAgent(id));

function pickAgent(id: string) {
  if (!state.agents.has(id)) { if (id === "captain") { tab = "settings"; settings.select("captain"); render(); } return; }
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
    h("button", { class: `tab${t === tab ? " active" : ""}`, onclick: () => {
      if (t === "settings" && focus.kind === "agent") settings.select(focus.id);
      tab = t; render();
    } },
      { activity: "Activity", agent: "Agent", chat: "Chat", approvals: "Approvals", settings: "Settings" }[t],
      t === "approvals" && approvals.pending ? h("span", { class: "badge" }, String(approvals.pending)) : null)));
  writeHash();
  panelRoot.replaceChildren({ activity: actRoot, agent: agentRoot, chat: chatRoot, approvals: approvalsRoot, settings: settingsRoot }[tab]);
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
  const pct = Math.min(100, (state.spend.today / Math.max(state.spend.cap, 0.01)) * 100);
  spendFill.style.width = `${pct}%`;
  spendFill.dataset.level = pct > 90 ? "high" : pct > 60 ? "mid" : "low";
  spendText.textContent = `${money(state.spend.today)} of $${state.spend.cap.toFixed(2)} today`;
  const working = [...state.agents.values()].filter((a) => a.status === "working").length;
  statusPill.textContent = state.clockedOut ? "🌙 Clocked out" : working ? `● ${working} working` : "● Open";
  statusPill.dataset.state = state.clockedOut ? "closed" : working ? "busy" : "open";
  demoBadge.classList.toggle("hidden", !state.demo);
  const n = approvals.pending;
  awaiting.textContent = `${n} awaiting you`;
  awaiting.classList.toggle("hidden", n === 0);
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
    }
  }
  requestAnimationFrame(frame);
}

// ---- data -----------------------------------------------------------------------------------
async function loadState() {
  const snap: Snapshot = await (await fetch("/api/state")).json();
  state.load(snap);
  approvals.items = (snap as any).approvals ?? [];
  scene?.sync();
  render();
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (m) => {
    const ev: OfficeEvent = JSON.parse(m.data);
    if (ev.type === "roster_updated") { loadState(); }
    if (ev.type === "approval_requested" || ev.type === "approval_decided") { approvals.refresh(); }
    state.apply(ev);
    scene?.handle(ev);
    dirty = true;
    if (!ev.type.endsWith("_delta")) {
      structural = structural || tab === "activity" || tab === "chat";
    }
  };
  ws.onclose = () => setTimeout(async () => { await loadState().catch(() => {}); connect(); }, 1500);
}

state.on(() => { dirty = true; });

async function boot() {
  await loadState();
  readHash();
  const overlay = new Overlay(overlayLayer, state, () => scene);
  new Phaser.Game({
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
  connect();
  requestAnimationFrame(frame);
}

boot().catch((e) => {
  document.getElementById("app")!.replaceChildren(h("div", { class: "boot-error" },
    h("h2", {}, "Can't reach the office server."), h("p", {}, "Start it with ", h("code", {}, "uv run hq serve --demo"), " and reload."),
    h("pre", {}, String(e))));
});
