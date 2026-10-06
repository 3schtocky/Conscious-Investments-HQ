import { test } from "node:test";
import assert from "node:assert/strict";
import { gapSentence, indexReturn, shortDate, trackPoints, vsLine } from "../src/ui/portfolioModel.ts";

test("the track places entry, now and target along one line", () => {
  const t = trackPoints(100, 125, 150);
  assert.equal(t.entry, 0);
  assert.equal(t.now, 0.5);
  assert.equal(t.target, 1);
});

test("a position under water still fits: the track stretches to include it", () => {
  const t = trackPoints(100, 80, 150);
  assert.equal(t.now, 0);
  assert.ok(t.entry > 0 && t.entry < 0.5);
  assert.equal(t.target, 1);
});

test("a position past its target puts now at the end of the track", () => {
  const t = trackPoints(100, 170, 150);
  assert.equal(t.now, 1);
  assert.ok((t.target as number) < 1);
});

test("no target: only entry and now", () => {
  assert.equal(trackPoints(100, 110, null).target, null);
  assert.equal(trackPoints(100, 100, null).now, 0, "no movement does not divide by zero");
});

test("dates are read as written", () => {
  assert.equal(shortDate("2026-09-16"), "Sep 16");
  assert.equal(shortDate("2026-01-01"), "Jan 1");
  assert.equal(shortDate("soon"), "soon");
});

test("the sentence under the headline numbers", () => {
  assert.equal(gapSentence(5.2, "2026-09-14"), "Ahead of the S&P 500 by 5.2 points since Sep 14");
  assert.equal(gapSentence(-1.34, "2026-09-14"), "Behind the S&P 500 by 1.3 points since Sep 14");
  assert.equal(gapSentence(0.01, null), "Level with the S&P 500");
  assert.equal(gapSentence(null, null), "The comparison starts with the first position.");
});

test("index points read as returns", () => {
  assert.ok(Math.abs(indexReturn(108.2) - 0.082) < 1e-9);
  assert.ok(Math.abs(indexReturn(97.5) + 0.025) < 1e-9);
});

test("a position against the benchmark", () => {
  assert.equal(vsLine(0.143), "14.3 points ahead of the S&P 500 since entry");
  assert.equal(vsLine(-0.021), "2.1 points behind the S&P 500 since entry");
  assert.equal(vsLine(null), null);
});
