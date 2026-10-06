// The phone Portfolio's small decisions, free of the DOM so they can be tested.

/** Where entry, now and the target sit along one track, as fractions of its length (0 to 1). The
 *  track spans whatever range is needed, so a position under water or past its target still fits. */
export function trackPoints(entry: number, now: number, target: number | null): { entry: number; now: number; target: number | null } {
  const vals = [entry, now, ...(target ? [target] : [])];
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  const at = (v: number) => (v - lo) / span;
  return { entry: at(entry), now: at(now), target: target ? at(target) : null };
}

/** "2026-09-16" -> "Sep 16", read as written (no time zone shifting a date by a day). */
export function shortDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m[2]) - 1]} ${Number(m[3])}`;
}

/** The sentence under the two headline numbers. `lead` is in index points (portfolio minus benchmark). */
export function gapSentence(lead: number | null, since: string | null, benchmarkName = "S&P 500"): string {
  if (lead === null) return "The comparison starts with the first position.";
  const pts = Math.abs(lead).toFixed(1);
  const when = since ? ` since ${shortDate(since)}` : "";
  if (Math.abs(lead) < 0.05) return `Level with the ${benchmarkName}${when}`;
  return `${lead > 0 ? "Ahead of" : "Behind"} the ${benchmarkName} by ${pts} points${when}`;
}

/** A point on the indexed chart (100 = the start) as a return: 108.2 -> 0.082. */
export const indexReturn = (index: number): number => index / 100 - 1;

/** "14.3 points ahead of the S&P 500 since entry". */
export function vsLine(vs: number | null, benchmarkName = "S&P 500"): string | null {
  if (vs === null) return null;
  const pts = Math.abs(vs * 100).toFixed(1);
  return Math.abs(vs * 100) < 0.05 ? `Level with the ${benchmarkName} since entry` : `${pts} points ${vs > 0 ? "ahead of" : "behind"} the ${benchmarkName} since entry`;
}
