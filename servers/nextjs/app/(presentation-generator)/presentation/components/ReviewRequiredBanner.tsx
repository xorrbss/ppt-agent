"use client";
import React from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

interface ReviewRequiredBannerProps {
  /** 0-based positions of the slides that need review. */
  pages: readonly number[];
  selectedSlide: number;
  onSelectPage: (position: number) => void;
  /** Opens the existing quality-review flow scoped to the flagged pages. */
  onReview?: () => void;
}

/**
 * Shown above the slides when a deck was published with pages that need
 * review: the deck is usable, and this lists exactly which pages to look at.
 */
export function ReviewRequiredBanner({
  pages,
  selectedSlide,
  onSelectPage,
  onReview,
}: ReviewRequiredBannerProps) {
  const t = useTranslations("pageQualityReview");
  if (pages.length === 0) return null;
  return (
    <div
      role="status"
      data-testid="review-required-banner"
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-y border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-900"
    >
      <span className="inline-flex items-center gap-1.5 font-semibold">
        <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5" />
        {t("banner.title", { count: pages.length })}
      </span>
      <ul aria-label={t("banner.pagesLabel")} className="flex min-w-0 flex-wrap gap-1.5">
        {pages.map((position) => (
          <li key={position}>
            <button
              type="button"
              data-testid="review-required-page-chip"
              aria-label={t("banner.pageChipAria", { page: position + 1 })}
              aria-current={position === selectedSlide ? "true" : undefined}
              onClick={() => onSelectPage(position)}
              className={`rounded-full border px-2 py-0.5 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${
                position === selectedSlide
                  ? "border-amber-500 bg-amber-200"
                  : "border-amber-300 bg-white hover:bg-amber-100"
              }`}
            >
              {t("banner.pageChip", { page: position + 1 })}
            </button>
          </li>
        ))}
      </ul>
      {onReview && (
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid="review-required-start"
          onClick={onReview}
          className="ml-auto h-7 border-amber-300 bg-white text-xs text-amber-900 hover:bg-amber-100"
        >
          {t("banner.review")}
        </Button>
      )}
    </div>
  );
}

export default ReviewRequiredBanner;
