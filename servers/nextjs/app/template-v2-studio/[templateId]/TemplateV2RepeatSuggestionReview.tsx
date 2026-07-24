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
  if (!suggestions.length) return null;

  return (
    <div>
      <p className="text-xs font-semibold text-amber-200">
        Repeat-block suggestions
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
                  {suggestion.axis ?? "unknown"} ·{" "}
                  {suggestion.source_ids?.length ?? 0} elements · confidence{" "}
                  {typeof suggestion.confidence === "number"
                    ? suggestion.confidence.toFixed(2)
                    : "unknown"}
                  {suggestion.status ? ` · ${suggestion.status}` : ""}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
