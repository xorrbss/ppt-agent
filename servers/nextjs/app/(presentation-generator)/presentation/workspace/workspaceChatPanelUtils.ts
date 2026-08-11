export type QualityReviewScope = "all" | "current" | "subset";

export interface SlideRangeParseResult { indices: number[]; normalized: string }

export function parseQualityReviewSlideRange(value: string, slideCount?: number): SlideRangeParseResult {
  const input = value.trim();
  if (!input) throw new Error("Slide selection is required.");
  const seen = new Set<number>();
  for (const token of input.split(",")) {
    const match = /^(\d+)(?:\s*-\s*(\d+))?$/.exec(token.trim());
    if (!match) throw new Error("Use page selections such as 3, 5-8, 12.");
    const start = Number(match[1]); const end = Number(match[2] ?? match[1]);
    if (start < 1 || end < 1 || end < start) throw new Error("Invalid page range.");
    if (slideCount !== undefined && end > slideCount) throw new Error(`Page selection must be at most ${slideCount}.`);
    for (let page = start; page <= end; page += 1) { if (seen.has(page)) throw new Error(`Page ${page} is duplicated.`); seen.add(page); }
  }
  const pages = [...seen].sort((left, right) => left - right);
  const normalized: string[] = [];
  for (let cursor = 0; cursor < pages.length;) { const start = pages[cursor]; let end = start; while (pages[cursor + 1] === end + 1) { cursor += 1; end = pages[cursor]; } normalized.push(start === end ? String(start) : `${start}-${end}`); cursor += 1; }
  return { indices: pages.map((page) => page - 1), normalized: normalized.join(", ") };
}

export const CONNECTION_LABELS = { connecting: "Connecting", live: "Live", polling: "Reconnecting", offline: "Offline" };
