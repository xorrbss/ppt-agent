"use client";

import { useTranslations } from "next-intl";

export interface RepeatSuggestion {
  id?: string;
  status?: string;
  axis?: string;
  source_ids?: string[];
  confidence?: number;
}

export default function TemplateV2RepeatSuggestionReview({
  suggestions,
  state,
  acceptedIds,
  onAcceptedIdsChange,
}: {
  suggestions: RepeatSuggestion[];
  state: string;
  acceptedIds: string[];
  onAcceptedIdsChange: (ids: string[]) => void;
}) {
  const t = useTranslations("studio");
  if (!suggestions.length) return null;

  function axisLabel(axis: string | undefined) {
    if (axis === "horizontal") {
      return t("repeatSuggestions.axes.horizontal");
    }
    if (axis === "vertical") return t("repeatSuggestions.axes.vertical");
    return t("repeatSuggestions.axes.unknown");
  }

  function statusLabel(status: string | undefined) {
    if (status === "suggested") {
      return t("repeatSuggestions.statuses.suggested");
    }
    if (status === "applied") {
      return t("repeatSuggestions.statuses.applied");
    }
    if (status === "unapplied") {
      return t("repeatSuggestions.statuses.unapplied");
    }
    return t("repeatSuggestions.statuses.unknown");
  }

  return (
    <div>
      <p className="text-xs font-semibold text-amber-200">
        {t("repeatSuggestions.title")}
      </p>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-slate-300">
        {suggestions.map((suggestion, index) => {
          const suggestionId = suggestion.id;
          const selectable =
            state === "review_required" && typeof suggestionId === "string";
          return (
            <li key={suggestionId ?? index}>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  disabled={!selectable}
                  checked={
                    suggestion.status === "applied" ||
                    (typeof suggestionId === "string" &&
                      acceptedIds.includes(suggestionId))
                  }
                  onChange={(event) => {
                    if (!suggestionId) return;
                    onAcceptedIdsChange(
                      event.target.checked
                        ? [...acceptedIds, suggestionId]
                        : acceptedIds.filter((value) => value !== suggestionId)
                    );
                  }}
                />
                <span>
                  {t(
                    suggestion.status
                      ? "repeatSuggestions.summaryWithStatus"
                      : "repeatSuggestions.summary",
                    {
                      axis: axisLabel(suggestion.axis),
                      count: suggestion.source_ids?.length ?? 0,
                      confidence:
                        typeof suggestion.confidence === "number"
                          ? suggestion.confidence.toFixed(2)
                          : t("repeatSuggestions.confidenceUnknown"),
                      status: statusLabel(suggestion.status),
                    },
                  )}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
