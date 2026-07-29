export type PptxMode = "fidelity" | "hybrid";

export type PptxModeResolution =
  | { ok: true; value: PptxMode }
  | { ok: false };

/** Missing mode deliberately preserves the historical image-per-slide export. */
export function resolvePptxMode(value: unknown): PptxModeResolution {
  if (value === undefined) return { ok: true, value: "fidelity" };
  if (value === "fidelity" || value === "hybrid") {
    return { ok: true, value };
  }
  return { ok: false };
}

/** PDF callers retain their historical path and do not interpret PPTX options. */
export function resolveRequestedPptxMode(
  format: "pdf" | "pptx",
  value: unknown
): PptxModeResolution {
  return format === "pptx"
    ? resolvePptxMode(value)
    : { ok: true, value: "fidelity" };
}
