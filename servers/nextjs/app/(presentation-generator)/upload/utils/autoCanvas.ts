/**
 * Pure resolver for the "자동" (auto) canvas row. It never mutates
 * `PresentationConfig`; the caller decides what to write once a concrete
 * row id comes back. Geometry mirrors the picker row table in
 * docs/aspect-ratio-expansion-design-20260928.md (§ Picker rows).
 */
export type CanvasRowId =
  | "standard-4x3"
  | "wide-16x9"
  | "tall-9x16"
  | "tall-9x13"
  | "a4-landscape"
  | "a4-portrait"
  | "square-1x1"
  | "ultrawide-21x9";

export interface CanvasRowGeometry {
  widthPx: number;
  heightPx: number;
}

export const CANVAS_ROW_GEOMETRY: Readonly<Record<CanvasRowId, CanvasRowGeometry>> = {
  "standard-4x3": { widthPx: 960, heightPx: 720 },
  "wide-16x9": { widthPx: 1280, heightPx: 720 },
  "tall-9x16": { widthPx: 720, heightPx: 1280 },
  "tall-9x13": { widthPx: 720, heightPx: 1040 },
  "a4-landscape": { widthPx: 1188, heightPx: 840 },
  "a4-portrait": { widthPx: 840, heightPx: 1188 },
  "square-1x1": { widthPx: 720, heightPx: 720 },
  "ultrawide-21x9": { widthPx: 1680, heightPx: 720 },
};

export interface SourceCanvasProportions {
  width: number;
  height: number;
}

function logAspect(widthPx: number, heightPx: number): number {
  return Math.log(widthPx / heightPx);
}

/**
 * Nearest enabled row to `sourceCanvas` by absolute log-ratio distance
 * (`|ln(w/h) − ln(W/H)|`), which weighs a landscape/portrait mismatch the
 * same regardless of which side is wider. Falls back to `defaultRowId` when
 * no source canvas is known, and to the first enabled row when the default
 * itself is not enabled. Returns `null` only when nothing is enabled.
 */
export function nearestEnabledCanvasRow(
  enabledRowIds: readonly CanvasRowId[],
  sourceCanvas: SourceCanvasProportions | null,
  defaultRowId: CanvasRowId,
): CanvasRowId | null {
  if (enabledRowIds.length === 0) return null;
  if (
    !sourceCanvas ||
    !(sourceCanvas.width > 0) ||
    !(sourceCanvas.height > 0)
  ) {
    return enabledRowIds.includes(defaultRowId) ? defaultRowId : enabledRowIds[0];
  }
  const target = logAspect(sourceCanvas.width, sourceCanvas.height);
  let best = enabledRowIds[0];
  let bestDistance = Infinity;
  for (const rowId of enabledRowIds) {
    const geometry = CANVAS_ROW_GEOMETRY[rowId];
    const distance = Math.abs(logAspect(geometry.widthPx, geometry.heightPx) - target);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = rowId;
    }
  }
  return best;
}
