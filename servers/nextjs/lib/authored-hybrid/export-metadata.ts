import type { PresentationExportQualityReport } from "../presentation-export-quality.ts";

/**
 * Small, stable export summary for API consumers that do not need the full
 * element-level quality report. The detailed `quality` payload remains the
 * source of truth for the Studio review dialog.
 */
export const AUTHORED_HYBRID_EXPORT_METADATA_SCHEMA_VERSION =
  "presenton.authored-hybrid-export-metadata/v1" as const;

export interface AuthoredHybridExportMetadata {
  schemaVersion: typeof AUTHORED_HYBRID_EXPORT_METADATA_SCHEMA_VERSION;
  strategy: "authored-hybrid";
  status: PresentationExportQualityReport["status"];
  totalSlides: number;
  editableSlides: number;
  imageFallbackSlides: number;
  fallbackReasonCounts: Record<string, number>;
}

export function summarizeAuthoredHybridExport(
  quality: PresentationExportQualityReport
): AuthoredHybridExportMetadata {
  return {
    schemaVersion: AUTHORED_HYBRID_EXPORT_METADATA_SCHEMA_VERSION,
    strategy: "authored-hybrid",
    status: quality.status,
    totalSlides: quality.totalSlides,
    editableSlides: quality.editableSlides,
    imageFallbackSlides: quality.imageFallbackSlides,
    fallbackReasonCounts: { ...(quality.fallbackReasonCounts ?? {}) },
  };
}
