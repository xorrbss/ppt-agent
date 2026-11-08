/**
 * Verbosity to send for a generation request. A template-redesign deck carries
 * the source deck's prose, so the backend resolves an omitted verbosity to
 * text-heavy; the UI therefore leaves it out unless the user picked one in the
 * advanced settings. Every other deck sends the configured value unchanged.
 */
export function requestVerbosity(
  config:
    | {
        verbosity?: string | null;
        verbosityTouched?: boolean;
        svgTemplatePin?: unknown;
        svgTemplateMode?: string | null;
      }
    | null
    | undefined,
): string | undefined {
  if (!config) return undefined;
  const templateRedesign =
    Boolean(config.svgTemplatePin) && config.svgTemplateMode === "template-redesign";
  if (templateRedesign && !config.verbosityTouched) return undefined;
  return config.verbosity ?? undefined;
}

/**
 * Verbosity fields restored from a stored generation request. A stored value was
 * the request's explicit verbosity, so it is restored as a user choice
 * (`verbosityTouched`); otherwise the current fields are kept.
 */
export function restoredVerbosity<T extends string>(
  stored: unknown,
  current: { verbosity: T; verbosityTouched?: boolean },
  allowed: readonly T[],
): { verbosity: T; verbosityTouched?: boolean } {
  if (typeof stored === "string" && (allowed as readonly string[]).includes(stored)) {
    return { verbosity: stored as T, verbosityTouched: true };
  }
  return { verbosity: current.verbosity, verbosityTouched: current.verbosityTouched };
}
