import { test } from "node:test";
import assert from "node:assert/strict";
import { captionFor, freshLines, idleLine, involves, joinNames, pushLine } from "../src/ui/captionModel.ts";
import { FLOOR_VIEWS, lastActiveLabel, neighbourView, opensOnHome, swipeDir } from "../src/ui/mobileModel.ts";

const NAMES: Record<string, string> = { chief_of_staff: "Juno", er_lead: "Brian Washington", screen_lead: "Scout", cr_lead: "Harbor", quant_lead: "Sigma", quant_associate: "Delta", captain: "Stott" };
const who = { name: (id: string | null | undefined) => (id ? NAMES[id] ?? id : "Office") };
const ev = (o: Record<string, unknown>) => ({ id: 1, ts: 100, task_id: null, agent: null, ...o }) as never;

test("joinNames reads naturally", () => {
  assert.equal(joinNames(["A"]), "A");
  assert.equal(joinNames(["A", "B"]), "A and B");
  assert.equal(joinNames(["A", "B", "C"]), "A, B and C");
});

test("on the public site speech is hidden, so a chat says who is talking with whom", () => {
  assert.equal(captionFor(ev({ type: "chat", agent: "chief_of_staff", channel: "floor", recipients: ["screen_lead", "cr_lead"], text: "…" }), who), "Juno is talking with Scout and Harbor");
  assert.equal(captionFor(ev({ type: "chat", agent: "chief_of_staff", channel: "group:juno", recipients: [], text: "…" }), who), "Juno is speaking to everyone");
});

test("in the demo the scripted words are quoted", () => {
  assert.equal(captionFor(ev({ type: "chat", agent: "chief_of_staff", channel: "floor", recipients: ["er_lead"], text: "Brian, take Micron." }), who), "Juno to Brian Washington: “Brian, take Micron.”");
  assert.equal(captionFor(ev({ type: "captain_message", agent: "chief_of_staff", text: "Juno, initiate coverage on Micron." }), who), "Stott: “Juno, initiate coverage on Micron.”");
});

test("hand-offs, gatherings and finished work", () => {
  assert.equal(captionFor(ev({ type: "delegated", agent: "quant_lead", to: "quant_associate", job: "…" }), who), "Sigma handed work to Delta");
  assert.equal(captionFor(ev({ type: "meeting", participants: ["chief_of_staff", "er_lead", "cr_lead"] }), who), "Juno, Brian Washington and Harbor gathered in the Lobby");
  assert.equal(captionFor(ev({ type: "task_done", agent: "screen_lead" }), who), "Scout finished a task");
});

test("only an assignment is news; sub-jobs, movement and status changes make no caption", () => {
  assert.equal(captionFor(ev({ type: "task_started", agent: "er_lead", kind: "assignment", title: "An audit review" }), who), "Brian Washington started “An audit review”");
  assert.equal(captionFor(ev({ type: "task_started", agent: "er_lead", kind: "job", title: "x" }), who), null);
  assert.equal(captionFor(ev({ type: "move", agent: "er_lead", to: "lobby" }), who), null);
  assert.equal(captionFor(ev({ type: "status", agent: "er_lead", status: "working" }), who), null);
});

test("involves: the doer, the one it was done to, and everyone in a chat or a gathering", () => {
  assert.equal(involves(ev({ type: "chat", agent: "a", recipients: ["b", "c"] }), "c"), true);
  assert.equal(involves(ev({ type: "delegated", agent: "a", to: "b" }), "b"), true);
  assert.equal(involves(ev({ type: "meeting", participants: ["a", "b"] }), "b"), true);
  assert.equal(involves(ev({ type: "task_done", agent: "a" }), "b"), false);
});

test("the strip keeps the newest three lines, collapses repeats and drops stale ones", () => {
  let lines = pushLine([], { text: "a", ts: 1 });
  lines = pushLine(lines, { text: "a", ts: 2 });
  assert.equal(lines.length, 1);
  for (const t of ["b", "c", "d"]) lines = pushLine(lines, { text: t, ts: 10 });
  assert.deepEqual(lines.map((l) => l.text), ["b", "c", "d"]);
  assert.deepEqual(freshLines([{ text: "old", ts: 0 }, { text: "new", ts: 90_000 }], 100_000).map((l) => l.text), ["new"]);
});

test("the strip's resting line says what the office is doing", () => {
  const base = { workingNames: [], replaying: false, held: false, clockedOut: false };
  assert.equal(idleLine(base), "Quiet right now");
  assert.equal(idleLine({ ...base, workingNames: ["Vera"] }), "Vera is working");
  assert.equal(idleLine({ ...base, workingNames: ["Vera", "Scout"] }), "Vera and Scout are working");
  assert.equal(idleLine({ ...base, replaying: true }), "Replaying recent work");
  assert.equal(idleLine({ ...base, held: true }), "The office is paused");
});

test("swiping moves along the chip row and stops at the ends", () => {
  assert.equal(neighbourView(FLOOR_VIEWS, "floor", 1), "equity_research");
  assert.equal(neighbourView(FLOOR_VIEWS, "floor", -1), null);
  assert.equal(neighbourView(FLOOR_VIEWS, "lobby", 1), null);
  assert.equal(neighbourView(FLOOR_VIEWS, "audit", -1), "quant");
});

test("a swipe has to be sideways, long enough and quick; a scroll or a tap is not one", () => {
  assert.equal(swipeDir({ dx: -90, dy: 10, ms: 220 }), 1);     // finger moved left: next view
  assert.equal(swipeDir({ dx: 90, dy: 10, ms: 220 }), -1);
  assert.equal(swipeDir({ dx: -90, dy: 80, ms: 220 }), 0, "mostly vertical");
  assert.equal(swipeDir({ dx: -20, dy: 2, ms: 100 }), 0, "too short");
  assert.equal(swipeDir({ dx: -90, dy: 5, ms: 1500 }), 0, "a slow drag");
});

test("the app opens on the summary only when the office is quiet", () => {
  assert.equal(opensOnHome({ working: 0, replaying: false, touring: false }), true);
  assert.equal(opensOnHome({ working: 2, replaying: false, touring: false }), false);
  assert.equal(opensOnHome({ working: 0, replaying: true, touring: false }), false);
});

test("last-active wording", () => {
  const now = 1_000_000 * 1000;
  assert.equal(lastActiveLabel(null, now), "No recent activity");
  assert.equal(lastActiveLabel(1_000_000 - 30, now), "Active just now");
  assert.equal(lastActiveLabel(1_000_000 - 20 * 60, now), "Last active 20 minutes ago");
  assert.equal(lastActiveLabel(1_000_000 - 3 * 3600, now), "Last active 3 hours ago");
  assert.equal(lastActiveLabel(1_000_000 - 30 * 3600, now), "Last active yesterday");
});
