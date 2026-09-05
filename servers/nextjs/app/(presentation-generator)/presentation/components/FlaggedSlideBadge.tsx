"use client";
import React from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslations } from "next-intl";
import ToolTip from "@/components/ToolTip";
import {
  describeFlaggedSlideReason,
  type FlaggedSlideBadge as FlaggedSlideBadgeModel,
} from "../utils/flaggedSlides";

interface FlaggedSlideBadgeProps {
  badge: FlaggedSlideBadgeModel;
  /** 1-based slide number shown to the user. */
  slideNumber: number;
  /**
   * ``thumbnail`` is the tiny pill on a sidebar card (reason in the title
   * attribute); ``slide`` is the compact notice on the page itself with the
   * reason in a tooltip.
   */
  variant: "thumbnail" | "slide";
}

/**
 * Marks a slide the authored pipeline published flagged: a remade-or-
 * placeholder page the user must review rather than a failed deck.
 */
export function FlaggedSlideBadge({ badge, slideNumber, variant }: FlaggedSlideBadgeProps) {
  const t = useTranslations("flaggedSlides");
  const reason = describeFlaggedSlideReason(t, badge);
  const label = t("badge");
  const ariaLabel = t("ariaLabel", { index: slideNumber, reason });

  if (variant === "thumbnail") {
    return (
      <span
        role="img"
        aria-label={ariaLabel}
        title={ariaLabel}
        data-testid="flagged-slide-badge"
        data-flag-stage={badge.stage}
        className="pointer-events-auto absolute bottom-1 left-1 z-50 inline-flex h-[16px] items-center gap-0.5 rounded-full border border-amber-300 bg-amber-50 px-1.5 text-[9px] font-semibold leading-none text-amber-800 shadow-sm"
      >
        <AlertTriangle aria-hidden="true" className="h-2.5 w-2.5" />
        {label}
      </span>
    );
  }

  return (
    <div
      data-testid="flagged-slide-notice"
      data-flag-stage={badge.stage}
      className="absolute left-3 top-3 z-30"
    >
      <ToolTip content={reason}>
        <span
          role="status"
          aria-label={ariaLabel}
          className="inline-flex cursor-default items-center gap-1 rounded-full border border-amber-300 bg-amber-50/95 px-2.5 py-1 text-xs font-semibold text-amber-800 shadow-sm"
        >
          <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5" />
          {label}
        </span>
      </ToolTip>
    </div>
  );
}

export default FlaggedSlideBadge;
