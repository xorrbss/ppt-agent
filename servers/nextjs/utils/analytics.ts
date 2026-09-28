export function sanitizeAnalyticsError(
  error: unknown,
  fallback = "Unknown error",
): string {
  const raw =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : fallback;
  return raw.replace(/\s+/g, " ").trim().slice(0, 240) || fallback;
}

