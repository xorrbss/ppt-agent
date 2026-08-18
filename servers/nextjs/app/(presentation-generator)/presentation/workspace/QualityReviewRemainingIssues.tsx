import { useState } from "react";
import { useTranslations } from "next-intl";
import {
  issueLabel,
  type QualityReviewIssueDetail,
} from "./WorkspaceOperationResult";
import {
  remainingIssueRows,
  selectableSlideIndexes,
  toggleSelection,
} from "./qualityReviewIssueSelection";
import type { WorkspacePageRemakeRequester } from "./types";

const SHOWN_ISSUE_LIMIT = 8;

const CHECKBOX_CLASS =
  "h-3.5 w-3.5 shrink-0 accent-blue-600 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1";
const MOVE_BUTTON_CLASS =
  "rounded border border-blue-200 bg-white px-1.5 py-0.5 font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1";
const CHIP_BUTTON_CLASS =
  "rounded-md border border-blue-200 bg-white px-2 py-1 font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1";

/**
 * The residual issues a quality review left behind. Structured per-slide
 * details are authoritative; the flat `remaining_issues`/`issues` strings are
 * the degraded envelope and carry no slide attribution, so rows built from them
 * can never be selected for a remake.
 */
export function remainingIssueList(
  result: Record<string, unknown>,
  details: QualityReviewIssueDetail[],
): QualityReviewIssueDetail[] {
  if (details.length > 0) return details;
  const raw = Array.isArray(result.remaining_issues)
    ? result.remaining_issues
    : Array.isArray(result.issues)
      ? result.issues
      : [];
  return raw.flatMap((issue) => {
    const description = issueLabel(issue);
    return description === undefined ? [] : [{ description }];
  });
}

export function QualityReviewRemainingIssues({
  issues,
  totalIssueCount,
  remainingSlideIndexes,
  disabled = false,
  onFocusSlide,
  onRemakePage,
}: {
  issues: QualityReviewIssueDetail[];
  totalIssueCount: number;
  remainingSlideIndexes: number[];
  disabled?: boolean;
  onFocusSlide?: (slideIndex: number) => void;
  onRemakePage?: WorkspacePageRemakeRequester;
}) {
  const t = useTranslations("qualityReview.workspace");
  const [selected, setSelected] = useState<number[]>([]);

  const shownIssues = issues.slice(0, SHOWN_ISSUE_LIMIT);
  const rows = remainingIssueRows(shownIssues);
  const selectableIndexes = selectableSlideIndexes(shownIssues);
  // A result refresh can retire a slide that was ticked; only slides still on
  // screen may reach the remake request.
  const selection = selected.filter((slideIndex) =>
    selectableIndexes.includes(slideIndex),
  );
  // Slides the review reported as still failing without naming an issue; they
  // stay reachable as jump targets even though no row describes them.
  const unlistedSlideIndexes = remainingSlideIndexes.filter(
    (slideIndex) => !selectableIndexes.includes(slideIndex),
  );
  const hiddenIssueCount = Math.max(0, totalIssueCount - shownIssues.length);
  const allSelected =
    selectableIndexes.length > 0 && selection.length === selectableIndexes.length;
  const someSelected = selection.length > 0 && !allSelected;
  const remakeUnavailable = disabled || !onRemakePage || selection.length === 0;

  if (shownIssues.length === 0 && remainingSlideIndexes.length === 0) return null;

  return (
    <section
      className="mt-3 border-t border-blue-100 pt-2"
      aria-label={t("remainingIssues")}
      data-testid="quality-review-remaining-issues"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-semibold">{t("remainingIssues")}</h4>
        {selectableIndexes.length > 0 && (
          <label className="flex items-center gap-1.5 text-[11px] font-medium text-blue-800">
            <input
              type="checkbox"
              className={CHECKBOX_CLASS}
              checked={allSelected}
              disabled={disabled}
              ref={(node) => {
                if (node) node.indeterminate = someSelected;
              }}
              onChange={() =>
                setSelected(allSelected ? [] : [...selectableIndexes])
              }
              aria-label={t("selectAllIssueSlides")}
              data-testid="quality-review-select-all-slides"
            />
            {t("selectAllIssueSlides")}
          </label>
        )}
      </div>
      <ol className="mt-1.5 space-y-1.5">
        {rows.map(({ issue, selectable }, index) => {
          const { slideIndex } = issue;
          return (
            <li
              key={`${slideIndex ?? "unknown"}-${issue.description}-${index}`}
              className="rounded-md bg-white/70 px-2 py-1.5 text-[11px] text-blue-900"
            >
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                {selectable && slideIndex !== undefined && (
                  <input
                    type="checkbox"
                    className={CHECKBOX_CLASS}
                    checked={selection.includes(slideIndex)}
                    disabled={disabled}
                    onChange={() =>
                      setSelected((current) =>
                        toggleSelection(current, slideIndex),
                      )
                    }
                    aria-label={t("selectIssueSlide", { slide: slideIndex + 1 })}
                    data-testid={`quality-review-select-slide-${slideIndex + 1}`}
                  />
                )}
                <span>
                  {slideIndex === undefined
                    ? t("unattributedIssue", { description: issue.description })
                    : t("slideIssue", {
                        slide: slideIndex + 1,
                        description: issue.description,
                      })}
                </span>
                {slideIndex !== undefined && (
                  <button
                    type="button"
                    onClick={() => onFocusSlide?.(slideIndex)}
                    disabled={!onFocusSlide}
                    className={MOVE_BUTTON_CLASS}
                    data-testid={`quality-review-focus-remaining-slide-${slideIndex + 1}`}
                  >
                    {t("move")}
                  </button>
                )}
              </div>
              {issue.reason && (
                <p className="mt-0.5 text-blue-700">
                  {t("reason", { reason: issue.reason })}
                </p>
              )}
            </li>
          );
        })}
      </ol>
      {hiddenIssueCount > 0 && (
        <p className="mt-1 text-blue-700">
          {t("moreIssues", { count: hiddenIssueCount })}
        </p>
      )}
      {unlistedSlideIndexes.length > 0 && (
        <div
          className="mt-2 flex flex-wrap gap-1.5"
          aria-label={t("remainingSlidesAria")}
        >
          {unlistedSlideIndexes.map((slideIndex) => (
            <button
              key={slideIndex}
              type="button"
              onClick={() => onFocusSlide?.(slideIndex)}
              disabled={!onFocusSlide}
              className={CHIP_BUTTON_CLASS}
            >
              {t("moveToSlide", { slide: slideIndex + 1 })}
            </button>
          ))}
        </div>
      )}
      {selectableIndexes.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => onRemakePage?.(selection)}
            disabled={remakeUnavailable}
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
            data-testid="quality-review-batch-remake"
          >
            {t("batchRemakeSelected", { count: selection.length })}
          </button>
          <p className="text-[11px] text-blue-700">
            {t("batchRemakeSelectionHint")}
          </p>
        </div>
      )}
    </section>
  );
}
