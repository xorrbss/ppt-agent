/**
 * The single record guard for the frontend: a plain, non-null, non-array
 * object. Every "is this a JSON object" check outside browser-injected sources
 * composes these two helpers instead of re-stating the predicate, so the
 * array/null contract is enforced at exactly one point.
 */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function recordOrNull(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}
