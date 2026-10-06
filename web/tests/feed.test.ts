import { test } from "node:test";
import assert from "node:assert/strict";
import { buildFeed, dayLabel, filterFeed, groupByDay, shortAgo, type Thread } from "../src/ui/feedModel.ts";

const NAMES: Record<string, string> = { er_lead: "Brian Washington", er_associate: "Ledger", quant_lead: "Sigma", audit_lead: "Vera", chief_of_staff: "Juno" };
const WING: Record<string, string> = { er_lead: "equity_research", er_associate: "equity_research", quant_lead: "quant", audit_lead: "audit", chief_of_staff: "executive" };
const ctx = { name: (id: string | null | undefined) => (id ? NAMES[id] ?? id : "Office"), wingOf: (id: string) => WING[id] };
let ts = 1000;
const ev = (o: Record<string, unknown>) => ({ id: null, ts: (ts += 10), task_id: null, agent: null, ...o }) as never;
const started = (agent: string, title = "An audit review") => ev({ type: "task_started", agent, kind: "assignment", title });

test("an assignment becomes a thread; what its owner does next belongs to it until they finish", () => {
  const feed = buildFeed([
    started("er_lead", "Initiate coverage: Micron"),
    ev({ type: "delegated", agent: "er_lead", to: "er_associate", job: "…" }),
    ev({ type: "chat", agent: "er_lead", channel: "floor", recipients: ["quant_lead"], text: "…" }),
    ev({ type: "task_done", agent: "er_associate" }),
    ev({ type: "task_done", agent: "er_lead" }),
  ], ctx);
  assert.equal(feed.length, 1);
  const t = feed[0] as Thread;
  assert.equal(t.title, "Initiate coverage: Micron");
  assert.equal(t.wing, "equity_research");
  assert.deepEqual(t.steps.map((s) => s.kind), ["handoff", "talk", "returned", "done"]);
  assert.equal(t.steps[0].text, "Handed work to Ledger");
  assert.equal(t.steps[1].text, "Talked with Sigma");
  assert.equal(t.steps[2].text, "Ledger finished the job");
  assert.equal(t.steps[3].text, "Finished");
  assert.ok(t.endTs !== null);
});

test("an unfinished thread stays open", () => {
  const t = buildFeed([started("audit_lead")], ctx)[0] as Thread;
  assert.equal(t.endTs, null);
});

test("events outside any thread are short notes, and one repeated line is one line", () => {
  const feed = buildFeed([
    ev({ type: "chat", agent: "quant_lead", channel: "floor", recipients: ["audit_lead"], text: "…" }),
    ev({ type: "chat", agent: "quant_lead", channel: "floor", recipients: ["audit_lead"], text: "…" }),
    ev({ type: "task_done", agent: "audit_lead" }),
    ev({ type: "meeting", participants: ["chief_of_staff", "er_lead"] }),
  ], ctx);
  assert.deepEqual(feed.map((i) => (i.kind === "note" ? i.text : "thread")).sort(),
    ["Juno and Brian Washington gathered in the Lobby", "Sigma is talking with Vera", "Vera finished a task"]);
});

test("a second assignment replaces an unfinished one", () => {
  const feed = buildFeed([started("er_lead", "First"), started("er_lead", "Second"), ev({ type: "task_done", agent: "er_lead" })], ctx);
  const [a, b] = feed as Thread[];
  assert.equal(a.title, "Second");
  assert.ok(a.endTs && a.steps.at(-1)?.kind === "done");
  assert.equal(b.title, "First");
  assert.ok(b.endTs && b.steps.length === 0, "closed without a Finished line");
});

test("sub-jobs are not assignments", () => {
  assert.equal(buildFeed([ev({ type: "task_started", agent: "er_associate", kind: "job", title: "x" })], ctx).length, 0);
});

test("newest activity first", () => {
  const feed = buildFeed([started("er_lead", "Older"), started("quant_lead", "Newer")], ctx);
  assert.equal((feed[0] as Thread).title, "Newer");
});

test("filtering by wing keeps that wing's threads and notes only", () => {
  const feed = buildFeed([started("er_lead"), started("quant_lead"), ev({ type: "task_done", agent: "audit_lead" })], ctx);
  assert.equal(filterFeed(feed, "quant").length, 1);
  assert.equal(filterFeed(feed, "audit").length, 1);
  assert.equal(filterFeed(feed, null).length, 3);
});

test("short times", () => {
  const now = 100_000 * 1000;
  assert.equal(shortAgo(100_000 - 10, now), "now");
  assert.equal(shortAgo(100_000 - 150, now), "3m");
  assert.equal(shortAgo(100_000 - 3 * 3600, now), "3h");
  assert.equal(shortAgo(100_000 - 3 * 86400, now), "3d");
});

test("day headings", () => {
  const noon = (y: number, m: number, d: number) => new Date(y, m, d, 12).getTime();
  const now = noon(2026, 9, 5);
  assert.equal(dayLabel(noon(2026, 9, 5) / 1000, now), "Today");
  assert.equal(dayLabel(noon(2026, 9, 4) / 1000, now), "Yesterday");
  assert.equal(dayLabel(noon(2026, 9, 2) / 1000, now), "Friday");
  assert.equal(dayLabel(noon(2026, 8, 20) / 1000, now), "Sep 20");
  const g = groupByDay([{ kind: "note", ts: noon(2026, 9, 5) / 1000, text: "a", wing: null }, { kind: "note", ts: noon(2026, 9, 4) / 1000, text: "b", wing: null }], now);
  assert.deepEqual(g.map((x) => x.label), ["Today", "Yesterday"]);
});

test("inside a thread, words are quoted only where they are real", () => {
  const t = buildFeed([
    started("er_lead"),
    ev({ type: "chat", agent: "er_lead", channel: "floor", recipients: ["quant_lead"], text: "Sigma, please build the model." }),
    ev({ type: "chat", agent: "er_lead", channel: "floor", recipients: ["audit_lead"], text: "…" }),
  ], ctx)[0] as Thread;
  assert.deepEqual(t.steps.map((s) => s.text), ["To Sigma: “Sigma, please build the model.”", "Talked with Vera"]);
});
