// Type-only: this module stays free of runtime imports so it can be exercised
// under `node --test` without pulling the browser-side API client in.
import type { QualityReviewIssueDetail } from "./WorkspaceOperationResult";

export interface RemainingIssueRow {
  issue: QualityReviewIssueDetail;
  selectable: boolean;
}

/**
 * One checkbox per slide, never per issue: two issues on the same slide are one
 * remake, so only the first row of a slide carries the selection control.
 */
export function remainingIssueRows(
  issues: QualityReviewIssueDetail[],
): RemainingIssueRow[] {
  const claimed = new Set<number>();
  return issues.map((issue) => {
    if (issue.slideIndex === undefined || claimed.has(issue.slideIndex)) {
      return { issue, selectable: false };
    }
    claimed.add(issue.slideIndex);
    return { issue, selectable: true };
  });
}

export function selectableSlideIndexes(
  issues: QualityReviewIssueDetail[],
): number[] {
  return Array.from(
    new Set(
      issues.flatMap((issue) =>
        issue.slideIndex === undefined ? [] : [issue.slideIndex],
      ),
    ),
  ).sort((left, right) => left - right);
}

export function toggleSelection(
  selected: readonly number[],
  slideIndex: number,
): number[] {
  return selected.includes(slideIndex)
    ? selected.filter((value) => value !== slideIndex)
    : [...selected, slideIndex].sort((left, right) => left - right);
}
