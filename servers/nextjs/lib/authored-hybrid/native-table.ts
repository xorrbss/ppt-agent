import type { AuthoredHybridBounds } from "./schema.ts";
import type { BrowserElementObservation } from "./observation.ts";
import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";

export const AUTHORED_NATIVE_TABLE_SCHEMA_VERSION = "authored-native-table/v1" as const;
export const AUTHORED_NATIVE_TABLE_FONT_SIZE_PRECISION_PT = 0.001 as const;
type HorizontalAlignment = "left" | "center" | "right" | "justify";
type VerticalAlignment = "top" | "middle" | "bottom";
type BorderEdge = "top" | "right" | "bottom" | "left";

export interface AuthoredNativeTableBorderV1 { readonly color: string; readonly widthPt: number; }
export interface AuthoredNativeTableCellStyleV1 {
  readonly fill?: string;
  readonly marginsPt?: Readonly<Record<BorderEdge, number>>;
  readonly borders?: Readonly<Partial<Record<BorderEdge, AuthoredNativeTableBorderV1>>>;
  readonly horizontalAlign?: HorizontalAlignment;
  readonly verticalAlign?: VerticalAlignment;
}
export interface AuthoredNativeTablePayloadV1 {
  readonly schemaVersion: typeof AUTHORED_NATIVE_TABLE_SCHEMA_VERSION | "authored-native-table/v2";
  readonly sourceFitPolicy?: "composer-source-fit/v2";
  readonly rows: readonly (readonly {
    readonly paragraphs?: readonly { readonly runs: readonly { readonly text: string; readonly fontFamily: string; readonly fontSizePt: number; readonly weight: number; readonly italic?: boolean; readonly underline?: boolean; readonly color: string; readonly language?: string }[]; readonly alignment?: HorizontalAlignment; readonly lineHeightPt?: number; readonly spaceBeforePt?: number; readonly spaceAfterPt?: number }[];
    readonly text: string; readonly fill?: string; readonly textColor?: string;
    readonly bold?: boolean; readonly fontFamily?: string; readonly fontSizePt?: number;
  }[])[];
  readonly columnWidths?: readonly number[];
  readonly rowHeightsPt?: readonly number[];
  readonly mergedCells?: readonly { readonly row: number; readonly column: number; readonly rowSpan: number; readonly columnSpan: number; }[];
  readonly cellStyles?: readonly (readonly AuthoredNativeTableCellStyleV1[])[];
  readonly headerRows?: number; readonly fontFamily?: string; readonly fontSizePt?: number;
}
export interface AuthoredHybridNativeTableSource {
  readonly id: string; readonly domPath: string; readonly tagName: string;
  readonly sourceIndex: number; readonly zOrder: number; readonly cssZIndex: number | null;
  readonly bounds: AuthoredHybridBounds; readonly rotationDeg: 0; readonly opacity: 1;
  readonly parentElementId?: string | null;
  readonly classification: { readonly mode: "native"; readonly kind: "table"; readonly confidence: "safe" };
  readonly table: AuthoredNativeTablePayloadV1;
}

const COLOR = /^#[0-9a-f]{6}$/i;
const TABLE_FEATURES = new Set(["table", "table-v1", "cell-text", "cell-fill", "cell-font", "cell-border", "cell-alignment", "merged-cells", "row-height"]);
const BORDER_EDGES: readonly BorderEdge[] = ["top", "right", "bottom", "left"];
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function onlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean { const keys = new Set(allowed); return Object.keys(value).every((key) => keys.has(key)); }
function normalizedColor(value: unknown): string | undefined | null { return value == null ? undefined : typeof value === "string" && COLOR.test(value) ? `#${value.slice(1).toUpperCase()}` : null; }
function fontFamilyValue(value: unknown): string | undefined | null { return value == null ? undefined : typeof value === "string" && value.trim().length > 0 && value.length <= 128 ? value.trim() : null; }
function canonicalFontSizePt(value: number): number { return Math.round(value / AUTHORED_NATIVE_TABLE_FONT_SIZE_PRECISION_PT) * AUTHORED_NATIVE_TABLE_FONT_SIZE_PRECISION_PT; }
function fontSizeValue(value: unknown, floor = 6): number | undefined | null { return value == null ? undefined : typeof value === "number" && Number.isFinite(value) && value >= floor && value <= 72 ? canonicalFontSizePt(value) : null; }
function positiveVector(value: unknown, count: number): readonly number[] | undefined | null {
  if (value == null) return undefined;
  return Array.isArray(value) && value.length === count && value.every((item) => typeof item === "number" && Number.isFinite(item) && item > 0) ? Object.freeze(value as number[]) : null;
}
function parseCellStyle(value: unknown): AuthoredNativeTableCellStyleV1 | null {
  if (!record(value) || !onlyKeys(value, ["fill", "borders", "horizontalAlign", "verticalAlign", "marginsPt"])) return null;
  const fill = normalizedColor(value.fill); const horizontal = value.horizontalAlign; const vertical = value.verticalAlign;
  if (fill === null || (horizontal !== undefined && !["left", "center", "right", "justify"].includes(String(horizontal))) || (vertical !== undefined && !["top", "middle", "bottom"].includes(String(vertical)))) return null;
  let marginsPt: Record<BorderEdge, number> | undefined;
  if (value.marginsPt !== undefined) {
    const candidate = value.marginsPt;
    if (!record(candidate) || !onlyKeys(candidate, BORDER_EDGES) || BORDER_EDGES.some((edge) => typeof candidate[edge] !== "number" || !Number.isFinite(candidate[edge]) || (candidate[edge] as number) < 0 || (candidate[edge] as number) > 720)) return null;
    marginsPt = candidate as Record<BorderEdge, number>;
  }
  let borders: Partial<Record<BorderEdge, AuthoredNativeTableBorderV1>> | undefined;
  if (value.borders !== undefined) {
    if (!record(value.borders) || !onlyKeys(value.borders, BORDER_EDGES)) return null;
    borders = {};
    for (const edge of BORDER_EDGES) {
      const border = value.borders[edge]; if (border === undefined) continue;
      if (!record(border) || !onlyKeys(border, ["color", "widthPt"])) return null;
      const color = normalizedColor(border.color);
      if (typeof color !== "string" || typeof border.widthPt !== "number" || !Number.isFinite(border.widthPt) || border.widthPt <= 0 || border.widthPt > 20) return null;
      borders[edge] = Object.freeze({ color, widthPt: border.widthPt });
    }
  }
  return Object.freeze({ ...(marginsPt ? { marginsPt: Object.freeze(marginsPt) } : {}), ...(fill ? { fill } : {}), ...(borders ? { borders: Object.freeze(borders) } : {}), ...(horizontal ? { horizontalAlign: horizontal as HorizontalAlignment } : {}), ...(vertical ? { verticalAlign: vertical as VerticalAlignment } : {}) });
}

export function parseAuthoredNativeTablePayload(value: unknown): AuthoredNativeTablePayloadV1 | null {
  const sourceFit = record(value) && value.schemaVersion === "authored-native-table/v2" && value.sourceFitPolicy === "composer-source-fit/v2";
  const floor = sourceFit ? 5.25 : 6;
  if (!record(value) || !onlyKeys(value, ["sourceFitPolicy", "schemaVersion", "rows", "columnWidths", "rowHeightsPt", "mergedCells", "cellStyles", "headerRows", "fontFamily", "fontSizePt"]) || (!sourceFit && ((value.sourceFitPolicy != null) || (value.schemaVersion !== undefined && value.schemaVersion !== AUTHORED_NATIVE_TABLE_SCHEMA_VERSION))) || !Array.isArray(value.rows) || value.rows.length < 1 || value.rows.length > 100) return null;
  const width = Array.isArray(value.rows[0]) ? value.rows[0].length : 0; if (width < 1 || width > 20) return null;
  const rows = value.rows.map((row) => {
    if (!Array.isArray(row) || row.length !== width) return null;
    return row.map((cell) => {
      if (!record(cell) || !onlyKeys(cell, ["paragraphs", "text", "fill", "textColor", "bold", "fontFamily", "fontSizePt"]) || typeof cell.text !== "string" || [...cell.text].length > 10_000) return null;
      const fill = normalizedColor(cell.fill); const textColor = normalizedColor(cell.textColor); const fontFamily = fontFamilyValue(cell.fontFamily); const fontSizePt = fontSizeValue(cell.fontSizePt, floor);
      if (fill === null || textColor === null || fontFamily === null || fontSizePt === null || (cell.bold != null && typeof cell.bold !== "boolean")) return null;
      let paragraphs: AuthoredNativeTablePayloadV1["rows"][number][number]["paragraphs"];
      if (cell.paragraphs != null) {
        if (!sourceFit || !Array.isArray(cell.paragraphs) || !cell.paragraphs.length || cell.paragraphs.length > 256) return null;
        for (const paragraph of cell.paragraphs) {
          if (!record(paragraph) || !Array.isArray(paragraph.runs) || !paragraph.runs.length || paragraph.runs.length > 512) return null;
          if (["lineHeightPt", "spaceBeforePt", "spaceAfterPt"].some((key) => paragraph[key] !== undefined && (typeof paragraph[key] !== "number" || !Number.isFinite(paragraph[key]) || (paragraph[key] as number) < (key === "lineHeightPt" ? .001 : 0) || (paragraph[key] as number) > 720))) return null;
          for (const run of paragraph.runs) if (!record(run) || typeof run.text !== "string" || !fontFamilyValue(run.fontFamily) || fontSizeValue(run.fontSizePt, floor) == null || !normalizedColor(run.color) || typeof run.weight !== "number" || run.weight < 100 || run.weight > 1000) return null;
        }
        paragraphs = cell.paragraphs as unknown as NonNullable<typeof paragraphs>;
        if (paragraphs.map((p) => p.runs.map((r) => r.text).join("")).join("\n") !== cell.text) return null;
      }
      return Object.freeze({ text: cell.text, ...(paragraphs ? { paragraphs } : {}), ...(fill ? { fill } : {}), ...(textColor ? { textColor } : {}), ...(cell.bold != null ? { bold: cell.bold } : {}), ...(fontFamily ? { fontFamily } : {}), ...(fontSizePt !== undefined ? { fontSizePt } : {}) });
    });
  });
  if (rows.some((row) => row === null || row.some((cell) => cell === null))) return null;
  const columnWidths = positiveVector(value.columnWidths, width); const rowHeightsPt = positiveVector(value.rowHeightsPt, rows.length);
  const headerRows = value.headerRows == null ? 0 : Number.isSafeInteger(value.headerRows) && (value.headerRows as number) >= 0 && (value.headerRows as number) <= rows.length ? value.headerRows as number : null;
  const fontFamily = fontFamilyValue(value.fontFamily); const fontSizePt = fontSizeValue(value.fontSizePt, floor);
  if (columnWidths === null || rowHeightsPt === null || headerRows === null || fontFamily === null || fontSizePt === null) return null;
  let cellStyles: readonly (readonly AuthoredNativeTableCellStyleV1[])[] | undefined;
  if (value.cellStyles !== undefined) {
    if (!Array.isArray(value.cellStyles) || value.cellStyles.length !== rows.length) return null;
    const parsed = value.cellStyles.map((row) => { if (!Array.isArray(row) || row.length !== width) return null; const styles = row.map(parseCellStyle); return styles.some((style) => style === null) ? null : Object.freeze(styles as AuthoredNativeTableCellStyleV1[]); });
    if (parsed.some((row) => row === null)) return null;
    cellStyles = Object.freeze(parsed as (readonly AuthoredNativeTableCellStyleV1[])[]);
  }
  const mergedCells: { row: number; column: number; rowSpan: number; columnSpan: number; }[] = []; const occupied = new Set<string>();
  if (value.mergedCells != null) {
    if (!Array.isArray(value.mergedCells)) return null;
    for (const item of value.mergedCells) {
      if (!record(item) || !onlyKeys(item, ["row", "column", "rowSpan", "columnSpan"])) return null;
      const { row, column, rowSpan, columnSpan } = item;
      if (!Number.isSafeInteger(row) || !Number.isSafeInteger(column) || !Number.isSafeInteger(rowSpan) || !Number.isSafeInteger(columnSpan) || (row as number) < 0 || (column as number) < 0 || (rowSpan as number) < 1 || (columnSpan as number) < 1 || (row as number) + (rowSpan as number) > rows.length || (column as number) + (columnSpan as number) > width || ((rowSpan as number) === 1 && (columnSpan as number) === 1)) return null;
      for (let r = row as number; r < (row as number) + (rowSpan as number); r += 1) for (let c = column as number; c < (column as number) + (columnSpan as number); c += 1) { const key = `${r}:${c}`; if (occupied.has(key) || ((r !== row || c !== column) && rows[r]![c]!.text !== "")) return null; occupied.add(key); }
      mergedCells.push({ row: row as number, column: column as number, rowSpan: rowSpan as number, columnSpan: columnSpan as number });
    }
  }
  return Object.freeze({ schemaVersion: sourceFit ? "authored-native-table/v2" : AUTHORED_NATIVE_TABLE_SCHEMA_VERSION, ...(sourceFit ? { sourceFitPolicy: "composer-source-fit/v2" as const } : {}), rows: Object.freeze(rows as AuthoredNativeTablePayloadV1["rows"]), ...(columnWidths ? { columnWidths } : {}), ...(rowHeightsPt ? { rowHeightsPt } : {}), ...(mergedCells.length ? { mergedCells: Object.freeze(mergedCells) } : {}), ...(cellStyles ? { cellStyles } : {}), ...(headerRows ? { headerRows } : {}), ...(fontFamily ? { fontFamily } : {}), ...(fontSizePt !== undefined ? { fontSizePt } : {}) });
}

function canonicalCellEvidence(table: AuthoredNativeTablePayloadV1) {
  return { rows: table.rows.map((row, rowIndex) => row.map((cell, columnIndex) => { const style = table.cellStyles?.[rowIndex]?.[columnIndex]; const fill = style?.fill ?? cell.fill; return { text: cell.text.replace(/\r\n?/g, "\n").trim(), ...(fill ? { fill: fill.toUpperCase() } : {}), textColor: (cell.textColor ?? "#202020").toUpperCase(), bold: Boolean(cell.bold) || rowIndex < (table.headerRows ?? 0), fontFamily: (cell.fontFamily ?? table.fontFamily ?? "Arial").normalize("NFC").trim().toLowerCase(), fontSizePt: canonicalFontSizePt(cell.fontSizePt ?? table.fontSizePt ?? 10) }; })) };
}
function canonicalBrowserEvidence(evidence: BrowserElementObservation["tableEvidence"], floor = 6) {
  if (!evidence || !Array.isArray(evidence.rows)) return null;
  for (const row of evidence.rows) for (const cell of row) if (!record(cell) || typeof cell.text !== "string" || (cell.fill !== undefined && (typeof cell.fill !== "string" || !COLOR.test(cell.fill))) || typeof cell.textColor !== "string" || !COLOR.test(cell.textColor) || typeof cell.bold !== "boolean" || typeof cell.fontFamily !== "string" || !cell.fontFamily.trim() || typeof cell.fontSizePt !== "number" || fontSizeValue(cell.fontSizePt, floor) === null) return null;
  return { rows: evidence.rows.map((row) => row.map((cell) => ({ text: cell.text.replace(/\r\n?/g, "\n").trim(), ...(cell.fill ? { fill: cell.fill.toUpperCase() } : {}), textColor: cell.textColor.toUpperCase(), bold: cell.bold, fontFamily: cell.fontFamily.normalize("NFC").trim().toLowerCase(), fontSizePt: canonicalFontSizePt(cell.fontSizePt) }))) };
}
export function prepareDeclaredNativeTables(declared: readonly DeclaredSemanticElementForObservation[], observed: readonly BrowserElementObservation[]) {
  const byId = new Map(observed.map((element, zOrder) => [element.id, { element, zOrder }])); const tables: AuthoredHybridNativeTableSource[] = []; const rejections = new Map<string, "unsupported-native-table" | "unsupported-table-css" | "table-observation-mismatch">();
  for (const item of declared) {
    if (item.kind !== "table") continue; const found = byId.get(item.elementId); const actual = found?.element; let table = parseAuthoredNativeTablePayload(item.payload);
    if (!table || !actual || actual.candidateKind !== "complex" || (item.requiredFeatures ?? []).some((feature) => !TABLE_FEATURES.has(feature))) { rejections.set(item.elementId, "unsupported-native-table"); continue; }
    if (Math.abs(actual.rotationDeg) > .001 || actual.opacity < .999 || actual.fallbackReasons.length !== 1 || actual.fallbackReasons[0] !== "complex-table") { rejections.set(item.elementId, "unsupported-table-css"); continue; }
    if (JSON.stringify(canonicalBrowserEvidence(actual.tableEvidence, table.sourceFitPolicy ? 5.25 : 6)) !== JSON.stringify(canonicalCellEvidence(table))) { rejections.set(item.elementId, "table-observation-mismatch"); continue; }
    if (table.sourceFitPolicy) {
      const runEvidence = (paragraphs: NonNullable<AuthoredNativeTablePayloadV1["rows"][number][number]["paragraphs"]> | undefined) => (paragraphs ?? []).map((p) => p.runs.map((r) => ({ text: r.text, fontFamily: r.fontFamily.toLowerCase(), fontSizePt: canonicalFontSizePt(r.fontSizePt), weight: r.weight, italic: Boolean(r.italic), underline: Boolean(r.underline), color: r.color.toUpperCase() })));
      if (JSON.stringify(table.mergedCells ?? []) !== JSON.stringify(actual.tableEvidence?.mergedCells ?? []) || table.rows.some((row, r) => row.some((cell, c) => JSON.stringify(runEvidence(cell.paragraphs)) !== JSON.stringify(runEvidence(actual.tableEvidence?.rows[r]?.[c]?.paragraphs))))) { rejections.set(item.elementId, "table-observation-mismatch"); continue; }
    }
    if (table.sourceFitPolicy && actual.tableEvidence) {
      const evidence = actual.tableEvidence;
      if (evidence.containsReplacedContent || evidence.unsupportedPaint) { rejections.set(item.elementId, "unsupported-table-css"); continue; }
      const measuredStyles = evidence.cellStyles?.map((row) => row.map((cell) => ({
        horizontalAlign: cell.horizontalAlign, verticalAlign: cell.verticalAlign,
        ...(cell.paddingPx ? { marginsPt: Object.fromEntries(Object.entries(cell.paddingPx).map(([edge, px]) => [edge, px * .75])) } : {}),
        borders: Object.fromEntries(Object.entries(cell.borders ?? {}).map(([edge, border]) => [edge, { color: border.color, widthPt: border.widthPx * .75 }])),
      })));
      const measuredRows = table.rows.map((row, r) => row.map((cell, c) => ({ ...cell, ...(cell.paragraphs ? { paragraphs: cell.paragraphs.map((paragraph, p) => ({ ...paragraph, ...evidence.rows[r]?.[c]?.paragraphs?.[p], runs: paragraph.runs })) } : {}) })));
      const measured = parseAuthoredNativeTablePayload({ ...table, rows: measuredRows, ...(evidence.columnWidthsPx ? { columnWidths: evidence.columnWidthsPx } : {}), ...(evidence.rowHeightsPx ? { rowHeightsPt: evidence.rowHeightsPx.map((px) => px * .75) } : {}), ...(measuredStyles ? { cellStyles: measuredStyles } : {}) });
      if (!measured) { rejections.set(item.elementId, "unsupported-table-css"); continue; }
      table = measured;
    }
    tables.push({ id: actual.id, domPath: actual.domPath, tagName: actual.tagName, sourceIndex: actual.sourceIndex, zOrder: found!.zOrder, cssZIndex: actual.cssZIndex, bounds: { px: actual.boundsPx, inches: { x: actual.boundsPx.x / 96, y: actual.boundsPx.y / 96, width: actual.boundsPx.width / 96, height: actual.boundsPx.height / 96 } }, rotationDeg: 0, opacity: 1, parentElementId: actual.parentElementId ?? null, classification: { mode: "native", kind: "table", confidence: "safe" }, table });
  }
  return { tables, rejections };
}

function esc(value: string) { return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }
const emu = (px: number) => Math.max(1, Math.round(px * 9525)); const pointEmu = (pt: number) => Math.max(1, Math.round(pt * 12700));
function borderXml(borders: AuthoredNativeTableCellStyleV1["borders"]): string {
  const names: Record<BorderEdge, string> = { top: "lnT", right: "lnR", bottom: "lnB", left: "lnL" };
  // DrawingML CT_TableCellProperties is sequence-sensitive. PowerPoint/Open XML
  // require lnL, lnR, lnT, lnB; semantic input order must never leak into OOXML.
  const ooxmlOrder: readonly BorderEdge[] = ["left", "right", "top", "bottom"];
  return ooxmlOrder.map((edge) => {
    const border = borders?.[edge];
    return border
      ? `<a:${names[edge]} w="${pointEmu(border.widthPt)}"><a:solidFill><a:srgbClr val="${border.color.slice(1)}"/></a:solidFill><a:prstDash val="solid"/></a:${names[edge]}>`
      : `<a:${names[edge]}><a:noFill/></a:${names[edge]}>`;
  }).join("");
}

function mergedPerimeterBorders(
  table: AuthoredNativeTablePayloadV1,
  merge: NonNullable<AuthoredNativeTablePayloadV1["mergedCells"]>[number],
  row: number,
  column: number,
): AuthoredNativeTableCellStyleV1["borders"] {
  const anchorBorders = table.cellStyles?.[merge.row]?.[merge.column]?.borders;
  if (row === merge.row && column === merge.column) return anchorBorders;
  const lastRow = merge.row + merge.rowSpan - 1;
  const lastColumn = merge.column + merge.columnSpan - 1;
  return {
    ...(row === merge.row && anchorBorders?.top ? { top: anchorBorders.top } : {}),
    ...(column === lastColumn && anchorBorders?.right ? { right: anchorBorders.right } : {}),
    ...(row === lastRow && anchorBorders?.bottom ? { bottom: anchorBorders.bottom } : {}),
    ...(column === merge.column && anchorBorders?.left ? { left: anchorBorders.left } : {}),
  };
}

function mirroredMergedNeighborBorders(
  table: AuthoredNativeTablePayloadV1,
  row: number,
  column: number,
  borders: AuthoredNativeTableCellStyleV1["borders"],
): AuthoredNativeTableCellStyleV1["borders"] {
  const resolved = { ...borders };
  for (const merge of table.mergedCells ?? []) {
    const anchorBorders = table.cellStyles?.[merge.row]?.[merge.column]?.borders;
    const lastRow = merge.row + merge.rowSpan - 1;
    const lastColumn = merge.column + merge.columnSpan - 1;
    if (row === merge.row + merge.rowSpan && column >= merge.column && column <= lastColumn && anchorBorders?.bottom) resolved.top = anchorBorders.bottom;
    if (row + 1 === merge.row && column >= merge.column && column <= lastColumn && anchorBorders?.top) resolved.bottom = anchorBorders.top;
    if (column === merge.column + merge.columnSpan && row >= merge.row && row <= lastRow && anchorBorders?.right) resolved.left = anchorBorders.right;
    if (column + 1 === merge.column && row >= merge.row && row <= lastRow && anchorBorders?.left) resolved.right = anchorBorders.left;
  }
  return resolved;
}

export function serializeNativeTableFrame(source: AuthoredHybridNativeTableSource, nonVisualId: number, alternativeText?: string): string {
  const { table } = source;
  const sourceFit = table.sourceFitPolicy === "composer-source-fit/v2";
  const columns = table.rows[0]!.length;
  const weights = table.columnWidths ?? Array(columns).fill(1);
  const total = weights.reduce((sum, value) => sum + value, 0);
  const grid = weights.map((weight) => `<a:gridCol w="${Math.round(emu(source.bounds.px.width) * weight / total)}"/>`).join("");
  const equalRowHeight = Math.round(emu(source.bounds.px.height) / table.rows.length);
  const merges = new Map((table.mergedCells ?? []).map((merge) => [`${merge.row}:${merge.column}`, merge]));
  const mergeByCell = new Map<string, NonNullable<AuthoredNativeTablePayloadV1["mergedCells"]>[number]>();
  const coverage = new Map<string, { hMerge: boolean; vMerge: boolean }>();
  for (const merge of table.mergedCells ?? []) {
    for (let row = merge.row; row < merge.row + merge.rowSpan; row += 1) {
      for (let column = merge.column; column < merge.column + merge.columnSpan; column += 1) {
        const key = `${row}:${column}`;
        mergeByCell.set(key, merge);
        if (row !== merge.row || column !== merge.column) {
          coverage.set(key, { hMerge: column > merge.column, vMerge: row > merge.row });
        }
      }
    }
  }
  const rows = table.rows.map((row, rowIndex) => {
    const height = table.rowHeightsPt ? pointEmu(table.rowHeightsPt[rowIndex]!) : equalRowHeight;
    const cells = row.map((cell, columnIndex) => {
      const key = `${rowIndex}:${columnIndex}`;
      const merge = merges.get(key);
      const containingMerge = mergeByCell.get(key);
      const covered = coverage.get(key);
      const style = containingMerge
        ? table.cellStyles?.[containingMerge.row]?.[containingMerge.column] ?? {}
        : table.cellStyles?.[rowIndex]?.[columnIndex] ?? {};
      const color = (cell.textColor ?? "#202020").slice(1).toUpperCase();
      const fillColor = style.fill ?? cell.fill;
      const fill = fillColor ? `<a:solidFill><a:srgbClr val="${fillColor.slice(1).toUpperCase()}"/></a:solidFill>` : "<a:noFill/>";
      const family = cell.fontFamily ?? table.fontFamily ?? "Arial";
      const size = sourceFit && covered ? 5.25 : canonicalFontSizePt(cell.fontSizePt ?? table.fontSizePt ?? 10);
      const alignment = { left: "l", center: "ctr", right: "r", justify: "just" }[style.horizontalAlign ?? "left"];
      const anchor = { top: "t", middle: "ctr", bottom: "b" }[style.verticalAlign ?? "middle"];
      const baseBorders = containingMerge
        ? mergedPerimeterBorders(table, containingMerge, rowIndex, columnIndex)
        : style.borders;
      const borders = mirroredMergedNeighborBorders(table, rowIndex, columnIndex, baseBorders);
      const mergeAttrs = [
        merge?.columnSpan && merge.columnSpan > 1 ? `gridSpan="${merge.columnSpan}"` : "",
        merge?.rowSpan && merge.rowSpan > 1 ? `rowSpan="${merge.rowSpan}"` : "",
        covered?.hMerge ? 'hMerge="1"' : "",
        covered?.vMerge ? 'vMerge="1"' : "",
      ].filter(Boolean).join(" ");
      const runProperties = `<a:rPr lang="en-US" sz="${Math.round(size * 100)}"${cell.bold || rowIndex < (table.headerRows ?? 0) ? ' b="1"' : ""}><a:solidFill><a:srgbClr val="${color}"/></a:solidFill><a:latin typeface="${esc(family)}"/><a:ea typeface="${esc(family)}"/><a:cs typeface="${esc(family)}"/></a:rPr>`;
      // Each authored line becomes its own paragraph; readback joins `<a:t>`
      // runs with "\n", so cell text round-trips exactly.
      const paragraphs = cell.paragraphs ? cell.paragraphs.map((paragraph, paragraphIndex) => {
        const align = { left: "l", center: "ctr", right: "r", justify: "just" }[paragraph.alignment ?? style.horizontalAlign ?? "left"];
        const runs = paragraph.runs.map((run) => {
          const properties = `<a:rPr lang="${esc(run.language ?? "en-US")}" sz="${Math.round(run.fontSizePt * 100)}"${run.weight >= 600 ? ' b="1"' : ''}${run.italic ? ' i="1"' : ''}${run.underline ? ' u="sng"' : ''}><a:solidFill><a:srgbClr val="${run.color.slice(1).toUpperCase()}"/></a:solidFill><a:latin typeface="${esc(run.fontFamily)}"/><a:ea typeface="${esc(run.fontFamily)}"/><a:cs typeface="${esc(run.fontFamily)}"/></a:rPr>`;
          return run.text.split("\n").map((text) => `<a:r>${properties}<a:t>${esc(text)}</a:t></a:r>`).join(`<a:br>${properties}</a:br>`);
        }).join("");
        const endSize = Math.max(5.25, ...paragraph.runs.map((run) => run.fontSizePt));
        const paragraphProperties = sourceFit ? `<a:pPr algn="${align}">${paragraph.lineHeightPt ? `<a:lnSpc><a:spcPts val="${Math.round(paragraph.lineHeightPt * 100)}"/></a:lnSpc>` : ""}<a:spcBef><a:spcPts val="${Math.round((paragraphIndex === 0 ? 0 : paragraph.spaceBeforePt ?? 0) * 100)}"/></a:spcBef><a:spcAft><a:spcPts val="${Math.round((paragraphIndex === cell.paragraphs!.length - 1 ? 0 : paragraph.spaceAfterPt ?? 0) * 100)}"/></a:spcAft></a:pPr>` : `<a:pPr algn="${align}"/>`;
        return `<a:p>${paragraphProperties}${runs}<a:endParaRPr${sourceFit ? ` sz="${Math.round(endSize * 100)}"` : ""}/></a:p>`;
      }).join("") : cell.text.split("\n").map((line) => `<a:p><a:pPr algn="${alignment}"/><a:r>${runProperties}<a:t>${esc(line)}</a:t></a:r><a:endParaRPr/></a:p>`).join("");
      // Table rows have minimum heights in PowerPoint. Explicit browser insets
      // and paragraph metrics prevent inherited Office defaults growing them.
      const sourceEdgeMargin = (edge: BorderEdge) => cell.text.length === 0 ? 0 : edge === "top" ? cell.paragraphs?.[0]?.spaceBeforePt ?? 0 : edge === "bottom" ? cell.paragraphs?.at(-1)?.spaceAfterPt ?? 0 : 0;
      // Office suppresses paragraph spacing at a text-body edge. CSS block
      // margins at that edge remain visible, so carry them as cell insets.
      const margins = sourceFit ? BORDER_EDGES.map((edge) => ` mar${{ top: "T", right: "R", bottom: "B", left: "L" }[edge]}="${Math.round(((style.marginsPt?.[edge] ?? 0) + sourceEdgeMargin(edge)) * 12700)}"`).join("") : "";
      const bodyProperties = sourceFit ? `<a:bodyPr lIns="0" tIns="0" rIns="0" bIns="0" anchor="${anchor}"><a:noAutofit/></a:bodyPr>` : '<a:bodyPr/>';
      let cellParagraphs = sourceFit && !cell.paragraphs ? paragraphs.replaceAll('<a:endParaRPr/>', `<a:endParaRPr sz="${Math.round(size * 100)}"/>`).replaceAll(`<a:pPr algn="${alignment}"/>`, `<a:pPr algn="${alignment}"><a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft></a:pPr>`) : paragraphs;
      if (sourceFit && cell.text.length === 0) {
        // An empty CSS paragraph has no glyph line box. A DrawingML empty
        // paragraph still imposes its font height, so inherited cell fonts
        // must not grow an otherwise empty source cell.
        cellParagraphs = `<a:p><a:pPr algn="${alignment}"><a:lnSpc><a:spcPts val="525"/></a:lnSpc><a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft><a:defRPr sz="525"/></a:pPr><a:endParaRPr sz="525"/></a:p>`;
      }
      return `<a:tc${mergeAttrs ? ` ${mergeAttrs}` : ""}><a:txBody>${bodyProperties}<a:lstStyle/>${cellParagraphs}</a:txBody><a:tcPr anchor="${anchor}"${margins}>${borderXml(borders)}${fill}</a:tcPr></a:tc>`;
    }).join("");
    return `<a:tr h="${height}">${cells}</a:tr>`;
  }).join("");
  return `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${nonVisualId}" name="DevAX hybrid table ${esc(source.id)}" descr="${esc(alternativeText ?? "presenton-authored-native-table")}"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="${Math.round(source.bounds.px.x * 9525)}" y="${Math.round(source.bounds.px.y * 9525)}"/><a:ext cx="${emu(source.bounds.px.width)}" cy="${emu(source.bounds.px.height)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="${(table.headerRows ?? 0) > 0 ? 1 : 0}"/><a:tblGrid>${grid}</a:tblGrid>${rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>`;
}
