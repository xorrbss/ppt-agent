/**
 * Markup-driven native table admission.
 *
 * A `<table data-native="table">` whose only raster reason is `complex-table`
 * is rebuilt as one editable DrawingML table from the browser's own cell
 * evidence. Unlike the declared path in native-table.ts, no server-side
 * payload is required: the observation is the source of truth, so the OOXML
 * cell text, fills, borders, alignment, and column geometry are exactly what
 * Chromium laid out. Anything the bounded serializer cannot carry (replaced
 * content, background images, dashed borders, merged cells, rotation or
 * transparency) leaves the table on the raster backplate with an explicit
 * rejection reason.
 */
import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";
import type {
  AuthoredHybridElement,
  AuthoredHybridTableCellStyleEvidence,
  AuthoredHybridTableEvidence,
} from "./schema.ts";
import {
  type AuthoredHybridNativeTableSource,
  type AuthoredNativeTablePayloadV1,
  parseAuthoredNativeTablePayload,
} from "./native-table.ts";
import { resolvePowerPointTypeface } from "./font-policy.ts";

export type ObservedNativeTableRejection =
  | "unsupported-native-table"
  | "unsupported-table-css"
  | "table-has-declared-children";

export interface ObservedNativeTablePreparation {
  readonly tables: readonly AuthoredHybridNativeTableSource[];
  readonly rejections: ReadonlyMap<string, ObservedNativeTableRejection>;
  /**
   * Compatibility-mode descendants (cell text candidates) whose paint is
   * carried by an admitted table. They must leave the native denominator so
   * the table is neither duplicated by text boxes nor counted as raster.
   */
  readonly ownedDescendantIds: ReadonlySet<string>;
}

export interface ObservedNativeTableOptions {
  /** Families packaged into the PPTX; other families map to Office substitutes. */
  readonly embeddedTypefaceFamilies?: readonly string[];
}

const DECLARED_TABLE_FEATURES = new Set([
  "table", "table-v1", "cell-text", "cell-fill", "cell-font", "cell-border",
  "cell-alignment", "merged-cells", "row-height",
]);
const PX_TO_PT = 0.75;
const MAX_BORDER_PT = 20;

function isObservedTableCandidate(element: AuthoredHybridElement): boolean {
  return (
    element.nativeHint === "table" &&
    element.classification.mode === "raster" &&
    element.classification.candidateKind === "complex" &&
    element.classification.reasons.includes("complex-table")
  );
}

function cellStyleFromEvidence(
  style: AuthoredHybridTableCellStyleEvidence | undefined,
): Record<string, unknown> | null {
  if (!style) return {};
  const borders: Record<string, { color: string; widthPt: number }> = {};
  for (const edge of ["top", "right", "bottom", "left"] as const) {
    const border = style.borders?.[edge];
    if (!border) continue;
    const widthPt = Math.round(border.widthPx * PX_TO_PT * 1000) / 1000;
    if (!(widthPt > 0) || widthPt > MAX_BORDER_PT) return null;
    borders[edge] = { color: border.color, widthPt };
  }
  return {
    ...(Object.keys(borders).length ? { borders } : {}),
    ...(style.paddingPx ? { marginsPt: Object.fromEntries(Object.entries(style.paddingPx).map(([edge, px]) => [edge, px * PX_TO_PT])) } : {}),
    horizontalAlign: style.horizontalAlign,
    verticalAlign: style.verticalAlign,
  };
}

function typefaceForCell(
  family: string,
  embeddedFamilies: ReadonlySet<string>,
): string {
  return resolvePowerPointTypeface([family], "Aptos", {
    preserveAuthoredFamilies: embeddedFamilies,
  });
}

/** Build the bounded table payload purely from browser evidence. */
export function tablePayloadFromEvidence(
  evidence: AuthoredHybridTableEvidence,
  options: ObservedNativeTableOptions = {},
): AuthoredNativeTablePayloadV1 | null {
  if (evidence.containsReplacedContent || evidence.unsupportedPaint) return null;
  const width = evidence.rows[0]?.length ?? 0;
  if (!evidence.rows.length || !width) return null;
  if (
    (evidence.columnWidthsPx && evidence.columnWidthsPx.length !== width) ||
    (evidence.rowHeightsPx && evidence.rowHeightsPx.length !== evidence.rows.length) ||
    (evidence.cellStyles &&
      (evidence.cellStyles.length !== evidence.rows.length ||
        evidence.cellStyles.some((row) => row.length !== width)))
  ) {
    return null;
  }
  const embedded = new Set(options.embeddedTypefaceFamilies ?? []);
  const rows = evidence.rows.map((row) =>
    row.map((cell) => {
      const fill = cell.fill ?? evidence.rootFill;
      return {
        text: cell.text,
        ...(evidence.sourceFitPolicy && cell.paragraphs ? { paragraphs: cell.paragraphs.map((p) => ({ ...p, runs: p.runs.map((run) => ({ ...run, fontFamily: typefaceForCell(run.fontFamily, embedded) })) })) } : {}),
        ...(fill ? { fill } : {}),
        textColor: cell.textColor,
        bold: cell.bold,
        fontFamily: typefaceForCell(cell.fontFamily, embedded),
        fontSizePt: cell.fontSizePt,
      };
    }),
  );
  let cellStyles: unknown[][] | undefined;
  if (evidence.cellStyles) {
    cellStyles = [];
    for (const row of evidence.cellStyles) {
      const converted = row.map(cellStyleFromEvidence);
      if (converted.some((style) => style === null)) return null;
      cellStyles.push(converted);
    }
  }
  return parseAuthoredNativeTablePayload({
    schemaVersion: evidence.sourceFitPolicy ? "authored-native-table/v2" : "authored-native-table/v1",
    ...(evidence.sourceFitPolicy ? { sourceFitPolicy: evidence.sourceFitPolicy, mergedCells: evidence.mergedCells ?? [] } : {}),
    rows,
    ...(evidence.columnWidthsPx ? { columnWidths: evidence.columnWidthsPx } : {}),
    ...(evidence.rowHeightsPx
      ? { rowHeightsPt: evidence.rowHeightsPx.map((px) => Math.round(px * PX_TO_PT * 1000) / 1000) }
      : {}),
    ...(cellStyles ? { cellStyles } : {}),
  });
}

function declaredRejection(
  elementId: string,
  declared: readonly DeclaredSemanticElementForObservation[] | undefined,
): ObservedNativeTableRejection | null {
  if (!declared) return null;
  const item = declared.find((entry) => entry.elementId === elementId);
  if (!item) return "unsupported-native-table";
  if (item.kind !== "table" && item.kind !== "complexIsland") return "unsupported-native-table";
  if ((item.requiredFeatures ?? []).some((feature) => !DECLARED_TABLE_FEATURES.has(feature))) {
    return "unsupported-native-table";
  }
  if ((item.childIds ?? []).length > 0) return "table-has-declared-children";
  return null;
}

function descendantIds(
  table: AuthoredHybridElement,
  elements: readonly AuthoredHybridElement[],
): string[] {
  const prefix = `${table.domPath} > `;
  return elements
    .filter((element) => element.id !== table.id && element.domPath.startsWith(prefix))
    .map((element) => element.id);
}

/**
 * Admit `data-native="table"` elements. Declared slides additionally require
 * the table to be a leaf in the declared graph; compatibility slides hand the
 * table ownership of its observed descendants instead.
 */
export function prepareObservedNativeTables(
  elements: readonly AuthoredHybridElement[],
  declared: readonly DeclaredSemanticElementForObservation[] | undefined,
  options: ObservedNativeTableOptions = {},
): ObservedNativeTablePreparation {
  const tables: AuthoredHybridNativeTableSource[] = [];
  const rejections = new Map<string, ObservedNativeTableRejection>();
  const ownedDescendantIds = new Set<string>();
  for (const element of elements) {
    if (!isObservedTableCandidate(element) || element.classification.mode !== "raster") continue;
    if (
      element.classification.reasons.length !== 1 ||
      Math.abs(element.rotationDeg) > 0.001 ||
      element.opacity < 0.999
    ) {
      rejections.set(element.id, "unsupported-table-css");
      continue;
    }
    const declaredProblem = declaredRejection(element.id, declared);
    if (declaredProblem) {
      rejections.set(element.id, declaredProblem);
      continue;
    }
    const table = element.tableEvidence
      ? tablePayloadFromEvidence(element.tableEvidence, options)
      : null;
    if (!table) {
      rejections.set(element.id, "unsupported-native-table");
      continue;
    }
    if (!declared) descendantIds(element, elements).forEach((id) => ownedDescendantIds.add(id));
    tables.push({
      id: element.id,
      domPath: element.domPath,
      tagName: element.tagName,
      sourceIndex: element.sourceIndex,
      zOrder: element.zOrder,
      cssZIndex: element.cssZIndex,
      bounds: element.bounds,
      rotationDeg: 0,
      opacity: 1,
      parentElementId: element.parentElementId ?? null,
      classification: { mode: "native", kind: "table", confidence: "safe" },
      table,
    });
  }
  return { tables, rejections, ownedDescendantIds };
}
