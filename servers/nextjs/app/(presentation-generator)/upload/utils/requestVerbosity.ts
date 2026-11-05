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
