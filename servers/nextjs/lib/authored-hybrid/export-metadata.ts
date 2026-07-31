export const AUTHORED_HYBRID_EXPORT_METADATA_VERSION = 1 as const;

export interface AuthoredHybridFallbackReasonSummary {
  reason: string;
  slideCount: number;
}

export interface AuthoredHybridExportMetadata {
  version: typeof AUTHORED_HYBRID_EXPORT_METADATA_VERSION;
  strategy: "authored-hybrid";
  totalSlides: number;
  editableSlides: number;
  imageFallbackSlides: number;
  fallbackReasons: AuthoredHybridFallbackReasonSummary[];
}

export interface AuthoredHybridSlideOutcome {
  editable: boolean;
  fallbackReason?: string;
}

/**
 * Keep export diagnostics small, deterministic, and safe to return to clients.
 * Only stable reason codes are accepted here; exception messages and authored
 * slide content must never cross this metadata boundary.
 */
export function summarizeAuthoredHybridExport(
  outcomes: readonly AuthoredHybridSlideOutcome[]
): AuthoredHybridExportMetadata {
  const reasonCounts = new Map<string, number>();
  let editableSlides = 0;

  for (const outcome of outcomes) {
    if (outcome.editable) {
      editableSlides += 1;
      continue;
    }
    const reason = outcome.fallbackReason?.trim() || "unknown";
    reasonCounts.set(reason, (reasonCounts.get(reason) ?? 0) + 1);
  }

  return {
    version: AUTHORED_HYBRID_EXPORT_METADATA_VERSION,
    strategy: "authored-hybrid",
    totalSlides: outcomes.length,
    editableSlides,
    imageFallbackSlides: outcomes.length - editableSlides,
    fallbackReasons: [...reasonCounts.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([reason, slideCount]) => ({ reason, slideCount })),
  };
}

const FALLBACK_REASON_LABELS: Readonly<Record<string, string>> = {
  "chrome-unavailable": "Chrome unavailable",
  "deck-time-budget-exhausted": "conversion time limit",
  "hybrid-processing-failed": "hybrid processing failure",
  "invalid-slide": "invalid slide data",
  "missing-html": "missing slide HTML",
  "no-editable-elements": "no editable elements",
  "no-layer-safe-elements": "no safely editable elements",
  "promotion-mismatch": "layer safety mismatch",
  "slide-limit-exceeded": "hybrid slide limit",
  "slide-processing-failed": "slide processing failure",
};

function fallbackReasonLabel(reason: string): string {
  if (reason.startsWith("preflight-")) {
    return `security preflight (${reason.slice("preflight-".length)})`;
  }
  return FALLBACK_REASON_LABELS[reason] ?? reason;
}

/** Human-readable UI summary without changing the existing download contract. */
export function formatAuthoredHybridExportSummary(
  metadata: AuthoredHybridExportMetadata
): string {
  const counts =
    `Editable ${metadata.editableSlides}/${metadata.totalSlides} slides; ` +
    `image fallback ${metadata.imageFallbackSlides}.`;
  if (!metadata.fallbackReasons.length) return counts;
  const reasons = metadata.fallbackReasons
    .map(
      ({ reason, slideCount }) =>
        `${fallbackReasonLabel(reason)}: ${slideCount}`
    )
    .join(", ");
  return `${counts} Reasons: ${reasons}.`;
}
