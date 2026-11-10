// A presentation whose generation driver the backend no longer claims (a
// removed Template V2 deck, an authored deck after the authored removal) is
// listed as an unsupported item and refused by every per-deck endpoint with
// 422 `unsupported_presentation_driver`. These helpers are the single place
// the frontend recognises both signals.

export const UNSUPPORTED_PRESENTATION_DRIVER_CODE =
  "unsupported_presentation_driver";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function mentionsUnsupportedDriver(value: unknown, depth = 0): boolean {
  if (typeof value === "string") {
    return value.split(":", 1)[0].trim() === UNSUPPORTED_PRESENTATION_DRIVER_CODE;
  }
  if (!isRecord(value) || depth > 2) return false;
  return ["code", "message", "detail", "error"].some((key) =>
    mentionsUnsupportedDriver(value[key], depth + 1),
  );
}

/** True for an `ApiError` (or error-shaped value) carrying the driver refusal. */
export function isUnsupportedPresentationDriverError(error: unknown): boolean {
  if (!isRecord(error) && !(error instanceof Error)) return false;
  const source = error as unknown as Record<string, unknown>;
  const status = source.status;
  if (typeof status === "number" && status >= 500) return false;
  return (
    mentionsUnsupportedDriver(source.code) ||
    mentionsUnsupportedDriver(source.detail) ||
    mentionsUnsupportedDriver(source.error) ||
    mentionsUnsupportedDriver(source.message)
  );
}

/**
 * True for a dashboard list row the backend projected as unsupported
 * (`UnsupportedPresentationListItem`), or one whose persisted identity no
 * longer resolves to a driver.
 */
export function isUnsupportedPresentationListItem(
  item: unknown,
  profileResolved: boolean,
): boolean {
  if (isRecord(item) && item.unsupported_template_format === true) return true;
  return !profileResolved;
}
