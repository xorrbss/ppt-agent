export type TemplateV2ImportReviewOutcome =
  | "editable-text"
  | "editable-container"
  | "manual-review";

export interface TemplateV2ImportReviewRow {
  slide: number;
  sourceId: string;
  name: string;
  kind: string;
  confidence: number;
  geometry: string;
  outcome: TemplateV2ImportReviewOutcome;
  reason: string | null;
}

export interface TemplateV2ImportReview {
  rows: TemplateV2ImportReviewRow[];
  total: number;
  truncated: boolean;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function label(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function geometry(shape: Record<string, unknown>): string {
  const values = ["x", "y", "width", "height"].map((key) =>
    finite(shape[key])
  );
  return values.every((value) => value !== null)
    ? `${values[0]}, ${values[1]} · ${values[2]} × ${values[3]} px`
    : "unknown";
}

function outcome(kind: unknown): TemplateV2ImportReviewOutcome {
  if (kind === "text") return "editable-text";
  if (kind === "container") return "editable-container";
  return "manual-review";
}

export function buildTemplateV2ImportReview(
  analysis: unknown,
  limit = 100
): TemplateV2ImportReview | null {
  const analysisRecord = record(analysis);
  const candidates = record(analysisRecord?.candidates);
  if (!Array.isArray(candidates?.slides)) return null;

  const rowLimit = Math.min(
    100,
    Math.max(0, Number.isSafeInteger(limit) ? limit : 100)
  );
  const rows: TemplateV2ImportReviewRow[] = [];
  let total = 0;
  candidates.slides.forEach((slideValue, slideIndex) => {
    const slide = record(slideValue);
    if (!slide || !Array.isArray(slide.shapes)) return;
    slide.shapes.forEach((shapeValue) => {
      const shape = record(shapeValue);
      if (!shape) return;
      total += 1;
      if (rows.length >= rowLimit) return;
      const kind = label(shape.kind, "unknown");
      const confidence = finite(shape.confidence);
      rows.push({
        slide: slideIndex + 1,
        sourceId: label(shape.source_id, `shape-${total}`),
        name: label(shape.name, "Unnamed shape"),
        kind,
        confidence:
          confidence === null ? 0 : Math.min(1, Math.max(0, confidence)),
        geometry: geometry(shape),
        outcome: outcome(kind),
        reason:
          typeof shape.unsupported_reason === "string"
            ? shape.unsupported_reason
            : null,
      });
    });
  });

  return { rows, total, truncated: rows.length < total };
}
