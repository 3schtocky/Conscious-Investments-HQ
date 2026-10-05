import { test } from "node:test";
import assert from "node:assert/strict";
import { actIndexAt, clock, followView, inviteDue, markerAt, shouldCut, skipTarget, UNLOCK_TEXT } from "../src/ui/demoMobileModel.ts";
import { captionFor } from "../src/ui/captionModel.ts";

// The real act times of the Micron tour.
const ACTS = [0, 28, 108, 132, 222, 248, 298].map((t, i) => ({ t, label: `Act ${i + 1}`, caption: "" }));
const DURATION = 340;

test("which act is playing", () => {
  assert.equal(actIndexAt(ACTS, 0), 0);
  assert.equal(actIndexAt(ACTS, 27.9), 0);
  assert.equal(actIndexAt(ACTS, 28), 1);
  assert.equal(actIndexAt(ACTS, 339), 6);
});

test("next goes to the following act, and stops at the last", () => {
  assert.equal(skipTarget(ACTS, 10, 1), 28);
  assert.equal(skipTarget(ACTS, 300, 1), null);
});

test("previous restarts the act you are in, unless you have only just begun it", () => {
  assert.equal(skipTarget(ACTS, 60, -1), 28);      // well into act 2: back to its start
  assert.equal(skipTarget(ACTS, 29, -1), 0);       // one second into act 2: back to act 1
  assert.equal(skipTarget(ACTS, 1, -1), 0);        // at the very start: stay at 0
});

test("a tap on the progress bar picks an act marker only when it is close to one", () => {
  const width = 340;   // one pixel per second makes the maths easy
  assert.equal(markerAt(ACTS, DURATION, 108 / 340, width), 108);
  assert.equal(markerAt(ACTS, DURATION, 115 / 340, width), 108, "within the slop");
  assert.equal(markerAt(ACTS, DURATION, 60 / 340, width), null, "nowhere near a marker");
});

const WING: Record<string, string> = { er_lead: "equity_research", er_associate: "equity_research", quant_lead: "quant", chief_of_staff: "executive", cr_lead: "client_relations" };
const wingOf = (id: string) => WING[id];

test("the camera follows the speaker's wing, the lobby for a gathering, the whole floor for Juno", () => {
  assert.equal(followView({ type: "chat", agent: "er_lead" }, wingOf), "equity_research");
  assert.equal(followView({ type: "delegated", agent: "quant_lead" }, wingOf), "quant");
  assert.equal(followView({ type: "meeting", agent: "chief_of_staff" }, wingOf), "lobby");
  assert.equal(followView({ type: "chat", agent: "chief_of_staff" }, wingOf), "floor");
});

test("walking and status changes do not move the camera", () => {
  assert.equal(followView({ type: "move", agent: "er_lead" }, wingOf), null);
  assert.equal(followView({ type: "status", agent: "er_lead" }, wingOf), null);
  assert.equal(followView({ type: "chat", agent: "nobody" }, wingOf), null);
});

test("a cut needs a change and a pause since the last cut", () => {
  assert.equal(shouldCut({ want: "quant", current: "quant", now: 100, lastCut: 0 }), false);
  assert.equal(shouldCut({ want: "quant", current: "equity_research", now: 100, lastCut: 98 }), false, "too soon");
  assert.equal(shouldCut({ want: "quant", current: "equity_research", now: 100, lastCut: 90 }), true);
  assert.equal(shouldCut({ want: null, current: "floor", now: 100, lastCut: 0 }), false);
});

test("the first-visit invitation shows once, on the floor, after a moment", () => {
  const base = { dismissed: false, touring: false, secondsOpen: 6, onFloor: true };
  assert.equal(inviteDue(base), true);
  assert.equal(inviteDue({ ...base, secondsOpen: 1 }), false);
  assert.equal(inviteDue({ ...base, dismissed: true }), false);
  assert.equal(inviteDue({ ...base, touring: true }), false);
  assert.equal(inviteDue({ ...base, onFloor: false }), false);
});

test("unlock messages and the clock", () => {
  assert.equal(UNLOCK_TEXT.model, "The bull, base and bear model is ready");
  assert.equal(clock(340), "5:40");
  assert.equal(clock(65.7), "1:05");
});

const NAMES: Record<string, string> = { quant_lead: "Sigma", captain: "Stott", er_lead: "Brian Washington" };
const who = { name: (id: string | null | undefined) => (id ? NAMES[id] ?? id : "Office") };
const ev = (o: Record<string, unknown>) => ({ id: 1, ts: 1, task_id: null, agent: null, ...o }) as never;

test("the demo's reports and approvals read as captions", () => {
  assert.equal(captionFor(ev({ type: "captain_report", agent: "er_lead", text: "The report is done." }), who), "Brian Washington to Stott: “The report is done.”");
  assert.equal(captionFor(ev({ type: "approval_requested", agent: "quant_lead", title: "Micron model v1" }), who), "Sigma asked Stott to approve: Micron model v1");
  assert.equal(captionFor(ev({ type: "approval_decided", agent: "quant_lead", decision: "approved", title: "Micron model v1" }), who), "Stott approved: Micron model v1");
});

test("on the public site there are no such words, and so no such captions", () => {
  assert.equal(captionFor(ev({ type: "captain_report", agent: "er_lead", text: "…" }), who), null);
});
