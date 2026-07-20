import type {
  AuthoredHybridFallbackReason,
  AuthoredHybridNativeKind,
  AuthoredHybridTextRole,
} from "./schema.ts";

export interface AuthoredHybridCandidateObservation {
  candidateKind: AuthoredHybridNativeKind | "complex";
  textRole?: AuthoredHybridTextRole | "unsupported";
  hasPayload: boolean;
  fallbackReasons: AuthoredHybridFallbackReason[];
}

export type AuthoredHybridClassification =
  | {
      mode: "native";
      kind: AuthoredHybridNativeKind;
      confidence: "safe";
    }
  | {
      mode: "raster";
      candidateKind: AuthoredHybridNativeKind | "complex";
      reasons: AuthoredHybridFallbackReason[];
    };

const FALLBACK_REASON_ORDER: readonly AuthoredHybridFallbackReason[] = [
  "extraction-error",
  "invalid-bounds",
  "outside-slide",
  "complex-table",
  "complex-chart",
  "complex-diagram",
  "svg-text",
  "complex-content",
  "clip-path",
  "mask",
  "filter",
  "backdrop-filter",
  "mix-blend-mode",
  "complex-transform",
  "transformed-ancestor",
  "pseudo-element",
  "external-paint",
  "overflow-clipped",
  "occluded",
  "unknown-z-order",
  "animated",
  "css-columns",
  "vertical-writing",
  "background-clip-text",
  "text-shadow",
  "decorated-text",
  "decorated-image",
  "unsupported-background",
  "unsupported-color",
  "unsupported-image-format",
  "unsupported-object-position",
  "unsupported-object-fit",
  "rounded-image",
  "unsupported-direction",
  "unsupported-opacity",
  "unsupported-role",
  "unsupported-shape",
  "ambiguous-whitespace",
  "run-extraction-error",
];

const FALLBACK_ORDER = new Map(
  FALLBACK_REASON_ORDER.map((reason, index) => [reason, index])
);

export function normalizeFallbackReasons(
  reasons: readonly AuthoredHybridFallbackReason[]
): AuthoredHybridFallbackReason[] {
  return [...new Set(reasons)].sort(
    (left, right) =>
      (FALLBACK_ORDER.get(left) ?? Number.MAX_SAFE_INTEGER) -
        (FALLBACK_ORDER.get(right) ?? Number.MAX_SAFE_INTEGER) ||
      left.localeCompare(right)
  );
}

/**
 * H1 intentionally uses a deny-by-default promotion policy. One unsafe element
 * becomes raster without changing the classification of any sibling.
 */
export function classifyAuthoredHybridCandidate(
  observation: AuthoredHybridCandidateObservation
): AuthoredHybridClassification {
  const reasons = [...observation.fallbackReasons];

  if (observation.candidateKind === "complex") {
    if (reasons.length === 0) reasons.push("complex-content");
  }
  if (!observation.hasPayload) {
    reasons.push("extraction-error");
  }
  if (
    observation.candidateKind === "text" &&
    observation.textRole === "unsupported"
  ) {
    reasons.push("unsupported-role");
  }

  const normalizedReasons = normalizeFallbackReasons(reasons);
  if (normalizedReasons.length > 0) {
    return {
      mode: "raster",
      candidateKind: observation.candidateKind,
      reasons: normalizedReasons,
    };
  }

  // Complex observations always collect a fallback reason above. Keep this
  // narrowing explicit so a future reason-policy change cannot create a
  // native "complex" classification.
  if (observation.candidateKind === "complex") {
    return {
      mode: "raster",
      candidateKind: "complex",
      reasons: ["complex-content"],
    };
  }

  return {
    mode: "native",
    kind: observation.candidateKind,
    confidence: "safe",
  };
}
