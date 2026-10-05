import { test } from "node:test";
import assert from "node:assert/strict";
import { isMobileLayout, MORE_ROWS, MTABS, titleFor } from "../src/ui/mobileModel.ts";

test("the phone layout is for visitors on a narrow screen", () => {
  assert.equal(isMobileLayout({ width: 393, visitor: true }), true);     // iPhone 15, portrait
  assert.equal(isMobileLayout({ width: 440, visitor: true }), true);     // largest iPhone, portrait
  assert.equal(isMobileLayout({ width: 844, visitor: true }), false);    // iPhone landscape: desktop layout
  assert.equal(isMobileLayout({ width: 1440, visitor: true }), false);
});

test("a signed-in Captain keeps the desktop layout, unless it is forced for testing", () => {
  assert.equal(isMobileLayout({ width: 393, visitor: false }), false);
  assert.equal(isMobileLayout({ width: 1440, visitor: false, forced: true }), true);
});

test("the tab bar is Floor, Feed, Portfolio, More", () => {
  assert.deepEqual(MTABS.map((t) => t.label), ["Floor", "Feed", "Portfolio", "More"]);
});

test("More lists everything that is not in the tab bar, and Contact is a link", () => {
  assert.deepEqual(MORE_ROWS.map((r) => r.label), ["Watchlist", "Newsletters", "Team", "Demo files", "Appearance", "Contact"]);
  assert.equal(MORE_ROWS.find((r) => r.id === "contact")!.kind, "link");
});

test("titles: none on the floor, the tab name elsewhere, the page name inside More", () => {
  assert.equal(titleFor("floor", null), "");
  assert.equal(titleFor("feed", null), "Feed");
  assert.equal(titleFor("more", null), "More");
  assert.equal(titleFor("more", "watchlist"), "Watchlist");
  assert.equal(titleFor("more", "news"), "Newsletters");
});
