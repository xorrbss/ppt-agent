// Pure helpers for the server-classified generation failure. No runtime
// imports so node:test can load it directly.

export const GENERATION_FAILURE_CLASSES = [
  "internal_defect",
  "provider_unstable",
  "quality_not_passed",
  "service_interrupted",
  "source_unprocessable",
  "limit_reached",
  "unknown",
] as const;

export type GenerationFailureClass = (typeof GENERATION_FAILURE_CLASSES)[number];

/** Terminal states in which a generate operation is described as a failure. */
export const GENERATION_FAILURE_STATUSES = ["failed", "dead_letter", "deadline_exceeded", "expired"] as const;

/** Failure states that the recovery plan endpoint accepts (matches the operation card). */
export const GENERATION_RESUMABLE_STATUSES = ["failed", "dead_letter", "deadline_exceeded"] as const;

/** Accepts only the contract values; anything else is treated as unclassified. */
export function parseGenerationFailureClass(value: unknown): GenerationFailureClass | null {
  return typeof value === "string" && (GENERATION_FAILURE_CLASSES as readonly string[]).includes(value)
    ? (value as GenerationFailureClass)
    : null;
}

/** Message key under `generationFailure.<key>.title|body`; null falls back to the legacy text. */
export function generationFailureKey(failureClass: unknown): GenerationFailureClass | null {
  return parseGenerationFailureClass(failureClass);
}

/** An internal defect cannot be recovered by submitting the same generation again. */
export function canResumeGenerationFailure(failureClass: unknown): boolean {
  return parseGenerationFailureClass(failureClass) !== "internal_defect";
}

/** Resuming is recommended for recoverable failures other than input or usage limits. */
export function isResumePrimary(failureClass: unknown): boolean {
  const parsed = parseGenerationFailureClass(failureClass);
  return canResumeGenerationFailure(parsed) && parsed !== "source_unprocessable" && parsed !== "limit_reached";
}

export function isGenerationFailureStatus(status: string | null | undefined): boolean {
  return (GENERATION_FAILURE_STATUSES as readonly string[]).includes(status ?? "");
}

/** The resume created from `failedOperationId`, if the activity list already shows it. */
export function findResumedGeneration<
  T extends { retryOfOperationId: string | null; presentationId: string },
>(items: readonly T[], failedOperationId: string): T | null {
  return items.find((item) => item.retryOfOperationId === failedOperationId) ?? null;
}
