// Number and label formatting shared by the demo's desktop panel and its phone readers.
export const ratingTone = (rating: string): string => (rating === "Outperform" ? "good" : rating === "Underperform" ? "warn" : "");
export const usd = (x: number) => `$${x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
/** Percent, with losses in brackets as in the research reports. */
export const pct = (x: number) => (x < 0 ? `(${Math.abs(x * 100).toFixed(1)}%)` : `${(x * 100).toFixed(1)}%`);
