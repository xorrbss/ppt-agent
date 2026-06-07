// Deterministic density step-down for high item counts (backlog #1): shrink type +
// spacing so dense adaptive slides fit the fixed 1280x720 box. Pure functions of
// block counts — no transform, no effects, no randomness/clock — so the editor and
// the headless export render identically, and the export DOM stays clean (only the
// font-size / padding token values change; the data-block-id leaves are untouched).
import type { AnyBlock } from "./parts";

const FS_H4 = "var(--fs-h4, 1.375rem)";
const FS_BODY = "var(--fs-body, 1.125rem)";
const FS_SMALL = "var(--fs-small, 0.95rem)";
const FS_CAPTION = "var(--fs-caption, 0.8rem)";

// card-grid: 7-8 cards render at 4 columns and overflow → smaller title/text +
// tighter padding/icon.
export const cardGridDensity = (n: number) => {
  const dense = n >= 7;
  return {
    titleFs: dense ? FS_BODY : FS_H4,
    textFs: dense ? FS_SMALL : FS_BODY,
    cardClass: dense ? "gap-2 p-4" : "gap-3 p-6",
    iconClass: dense ? "w-6 h-6" : "w-8 h-8",
  };
};

// comparison: 5-6 items in the busiest column overflow → two-level step-down that
// keeps the worst case readable while fitting 1280x720.
export const comparisonDensity = (cols: AnyBlock[]) => {
  const maxItems = cols.reduce(
    (m, c) => Math.max(m, Array.isArray(c.items) ? c.items.length : 0),
    0
  );
  const dense = maxItems >= 5;
  const veryDense = maxItems >= 6;
  return {
    headFs: dense ? FS_BODY : FS_H4,
    itemFs: veryDense ? FS_CAPTION : dense ? FS_SMALL : FS_BODY,
    headPad: dense ? "px-5 py-2.5" : "px-6 py-4",
    listClass: veryDense ? "gap-1.5 p-3" : dense ? "gap-2 p-4" : "gap-3 p-6",
    itemGap: veryDense ? "gap-2" : "gap-3",
  };
};

// table: 7-8 rows overflow → smaller font + tighter cell padding.
export const tableDensity = (nRows: number) => {
  const dense = nRows >= 7;
  return {
    fontSize: dense ? FS_SMALL : FS_BODY,
    thPad: dense ? "px-4 py-2" : "px-4 py-3",
    tdPad: dense ? "px-4 py-1.5" : "px-4 py-2.5",
  };
};
