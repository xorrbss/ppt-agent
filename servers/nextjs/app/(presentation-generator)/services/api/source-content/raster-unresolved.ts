/**
 * Placement of analysis.unresolved entries in the raster review.
 *
 * Entries are "page_N:<reason>" (blocking), "advisory:page_N:<reason>"
 * (non-blocking), or deck-level without a page prefix. A page lists only its
 * own entries plus deck-level blocking ones; deck-level advisories are shown
 * once for the whole deck, never repeated per page.
 */

const ADVISORY = "advisory:";
const PAGE = /^page_(\d+):/;

/** Deck-level: no vision reader, so every page keeps its text as pixels. */
export const VISION_STRUCTURER_UNAVAILABLE = "advisory:vision_structurer_unavailable";
/** Page-level reason prefix: the page used the heuristic plan (its text stays pixels). */
const HEURISTIC_PLAN = "heuristic_plan:";
const STRUCTURING_FAILED = "page_structuring_failed:";
/** Page-level reason prefix: the second (transcription) reader failed, so no line was confirmed. */
const TRANSCRIPTION_FAILED = "transcription_failed:";
const PAGE_KEPT_AS_PIXELS = [HEURISTIC_PLAN, STRUCTURING_FAILED, TRANSCRIPTION_FAILED];

export type RasterUnresolvedIssue = {
  code: "page_kept_as_pixels" | "unresolved_source_content";
  message: string;
  severity: "blocking" | "warning";
};

function pageOf(entry: string): { page: number | null; reason: string; advisory: boolean } {
  const advisory = entry.startsWith(ADVISORY);
  const rest = advisory ? entry.slice(ADVISORY.length) : entry;
  const match = PAGE.exec(rest);
  return match
    ? { page: Number(match[1]), reason: rest.slice(match[0].length), advisory }
    : { page: null, reason: rest, advisory };
}

export function visionReaderUnavailable(unresolved: readonly string[]): boolean {
  return unresolved.includes(VISION_STRUCTURER_UNAVAILABLE);
}

/** The page's own entries (one "kept as pixels" item at most) plus deck-level blocking ones. */
export function pageUnresolvedIssues(unresolved: readonly string[], pageNumber: number): RasterUnresolvedIssue[] {
  const issues: RasterUnresolvedIssue[] = [];
  let degraded = false;
  for (const entry of unresolved) {
    const { page, reason, advisory } = pageOf(entry);
    if (page === null ? advisory : page !== pageNumber) continue;
    if (PAGE_KEPT_AS_PIXELS.some((prefix) => reason.startsWith(prefix))) {
      if (!degraded) issues.push({ code: "page_kept_as_pixels", message: entry, severity: "warning" });
      degraded = true;
      continue;
    }
    issues.push({ code: "unresolved_source_content", message: entry, severity: advisory ? "warning" : "blocking" });
  }
  return issues;
}

/** Advisories other than the deck-level vision notice (which gets its own notice). */
export function otherAdvisoryCount(advisoryCount: number, unresolved: readonly string[]): number {
  return Math.max(0, advisoryCount - (visionReaderUnavailable(unresolved) ? 1 : 0));
}
