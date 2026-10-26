"use client";
import React from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";

import { hasUnreviewedDeckFlow, isReviewRequiredDeck } from "@/app/(presentation-generator)/presentation/utils/flaggedSlides";

/** "Needs review" marker for a dashboard card whose deck has flagged pages. */
export function ReviewRequiredCardBadge({ theme }: { theme: unknown }) {
  const t = useTranslations("pageQualityReview");
  const flags = useTranslations("flaggedSlides");
  if (!isReviewRequiredDeck(theme)) return null;
  return (
    <span
      data-testid="review-required-card-badge"
      title={[t("dashboardBadge.title"), hasUnreviewedDeckFlow(theme) ? flags("designAxis.deckFlowUnreviewed") : ""].filter(Boolean).join(" ")}
      className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-semibold leading-none text-amber-800"
    >
      <AlertTriangle aria-hidden="true" className="h-3 w-3" />
      {t("dashboardBadge.label")}
    </span>
  );
}

export default ReviewRequiredCardBadge;
