export const FALLBACK_TIME_ZONE = "UTC";

/**
 * Resolve the IANA time zone of the current runtime.
 *
 * In the browser this is the viewer's own zone, which is what every
 * user-facing timestamp must be rendered in. On the server it is the host
 * zone; that value is only used for the SSR pass, and the client provider
 * re-resolves on hydration so the viewer never sees the server's zone.
 */
export function resolveViewerTimeZone(): string {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof zone === "string" && zone.trim() ? zone : FALLBACK_TIME_ZONE;
  } catch {
    return FALLBACK_TIME_ZONE;
  }
}

const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/**
 * Parse a backend timestamp as an instant. Backend values are UTC, but older
 * rows were serialized without an offset; those are read as UTC instead of
 * being shifted as if they were local wall-clock time.
 */
export function parseBackendTimestamp(value: string | undefined): Date | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const normalized = HAS_OFFSET.test(trimmed) ? trimmed : `${trimmed}Z`;
  const timestamp = new Date(normalized);
  return Number.isFinite(timestamp.getTime()) ? timestamp : undefined;
}

/**
 * Format a backend timestamp as a date only, in the viewer's locale and zone.
 * Matches what `Date.prototype.toLocaleDateString()` produces for the locale.
 */
export function formatViewerDate(
  value: string | undefined,
  locale?: string,
): string | undefined {
  const timestamp = parseBackendTimestamp(value);
  if (!timestamp) return undefined;
  return new Intl.DateTimeFormat(locale).format(timestamp);
}

/**
 * Format a backend timestamp for display in the viewer's locale and zone.
 * No `timeZone` is passed on purpose: the runtime default is the viewer's.
 */
export function formatViewerTimestamp(
  value: string | undefined,
  locale?: string,
  options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  },
): string | undefined {
  const timestamp = parseBackendTimestamp(value);
  if (!timestamp) return undefined;
  return new Intl.DateTimeFormat(locale, options).format(timestamp);
}
