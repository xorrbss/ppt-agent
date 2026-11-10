import { isRecord } from "@/lib/guards";

/** Mirrors the picker rows admission can gate per style (A4 rows are excluded: their
 * availability comes from `getDocumentCanvasCapabilities()`, not the style catalogue). */
export type AuthoredCanvasRowId =
  | "wide-16x9"
  | "tall-9x16"
  | "tall-9x13"
  | "square-1x1"
  | "ultrawide-21x9";

export type AuthoredCanvasDisabledReason =
  | "canvas_stage_disabled"
  | "style_not_supported";

export interface AuthoredStyleSupportedCanvas {
  id: AuthoredCanvasRowId;
  enabled: boolean;
  reason: string | null;
}

const AUTHORED_CANVAS_ROW_IDS: readonly AuthoredCanvasRowId[] = [
  "wide-16x9",
  "tall-9x16",
  "tall-9x13",
  "square-1x1",
  "ultrawide-21x9",
];

export function normalizeSupportedCanvases(
  value: unknown,
): AuthoredStyleSupportedCanvas[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const normalized = value.flatMap((entry): AuthoredStyleSupportedCanvas[] => {
    if (!isRecord(entry)) return [];
    const id = entry.id;
    if (
      typeof id !== "string" ||
      !AUTHORED_CANVAS_ROW_IDS.includes(id as AuthoredCanvasRowId)
    ) {
      return [];
    }
    return [
      {
        id: id as AuthoredCanvasRowId,
        enabled: entry.enabled === true,
        reason: typeof entry.reason === "string" && entry.reason.trim() ? entry.reason.trim() : null,
      },
    ];
  });
  return normalized.length > 0 ? normalized : undefined;
}
