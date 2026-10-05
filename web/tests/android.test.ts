import { test } from "node:test";
import assert from "node:assert/strict";
import { backDepth, installPlan, isSamsungBrowser, nextBack, planHistory, platformOf, type NavState } from "../src/ui/androidModel.ts";

const PIXEL = "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36";
const SAMSUNG = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
const MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";

test("platforms", () => {
  assert.equal(platformOf(PIXEL), "android");
  assert.equal(platformOf(SAMSUNG), "android");
  assert.equal(platformOf(IPHONE), "ios");
  assert.equal(platformOf(MAC), "other");
  assert.equal(isSamsungBrowser(SAMSUNG), true);
  assert.equal(isSamsungBrowser(PIXEL), false);
});

const base = { platform: "android" as const, standalone: false, dismissed: false, secondsOpen: 20, canPrompt: false, samsung: false };

test("the install card: the real Install button when the browser offers it, the menu otherwise", () => {
  assert.equal(installPlan({ ...base, canPrompt: true }), "android-prompt");
  assert.equal(installPlan(base), "android-menu");
  assert.equal(installPlan({ ...base, samsung: true }), "samsung-menu");
  assert.equal(installPlan({ ...base, platform: "ios" }), "ios-share");
});

test("the install card waits, shows once, and never over an installed app or a desktop", () => {
  assert.equal(installPlan({ ...base, secondsOpen: 5 }), null);
  assert.equal(installPlan({ ...base, dismissed: true }), null);
  assert.equal(installPlan({ ...base, standalone: true }), null);
  assert.equal(installPlan({ ...base, platform: "other" }), null);
});

const at = (o: Partial<NavState>): NavState => ({ sheet: false, tab: "floor", sub: null, fileOpen: false, ...o });

test("Back peels one layer at a time: sheet, then file, then More page, then tab", () => {
  assert.equal(nextBack(at({ sheet: true, tab: "more", sub: "demo", fileOpen: true })), "sheet");
  assert.equal(nextBack(at({ tab: "more", sub: "demo", fileOpen: true })), "file");
  assert.equal(nextBack(at({ tab: "more", sub: "demo" })), "sub");
  assert.equal(nextBack(at({ tab: "more", sub: "watchlist" })), "sub");
  assert.equal(nextBack(at({ tab: "more" })), "floor");
  assert.equal(nextBack(at({ tab: "feed" })), "floor");
  assert.equal(nextBack(at({ tab: "portfolio" })), "floor");
});

test("From the Floor, Back is the browser's: it leaves the app", () => {
  assert.equal(nextBack(at({})), null);
  assert.equal(backDepth(at({})), 0);
});

test("the depth counts the Back presses that stay in the app", () => {
  assert.equal(backDepth(at({ tab: "feed" })), 1);
  assert.equal(backDepth(at({ tab: "more" })), 1);
  assert.equal(backDepth(at({ tab: "more", sub: "news" })), 2);
  assert.equal(backDepth(at({ tab: "more", sub: "demo", fileOpen: true })), 3);
  assert.equal(backDepth(at({ sheet: true, tab: "more", sub: "demo", fileOpen: true })), 4);
  assert.equal(backDepth(at({ sheet: true })), 1, "the sheet over the Floor");
});

test("history follows the depth: push when deeper, step back when the app closed a layer itself", () => {
  assert.deepEqual(planHistory(0, 2), { push: 2, pop: 0 });
  assert.deepEqual(planHistory(3, 1), { push: 0, pop: 2 });
  assert.deepEqual(planHistory(2, 2), { push: 0, pop: 0 });
});
