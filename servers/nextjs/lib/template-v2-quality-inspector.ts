import type { JsonRecord } from "./template-v2-studio.ts";

export const TEMPLATE_V2_QUALITY_MIN_FONT_SIZE = 9;
export const TEMPLATE_V2_QUALITY_MAX_LEAVES = 24;
export const TEMPLATE_V2_QUALITY_MAX_TABLE_COLUMNS = 8;
export const TEMPLATE_V2_QUALITY_MIN_CONTRAST = 4.5;

export type TemplateV2QualityReasonCode =
  | "TEXT_OVERFLOW"
  | "TEXT_BELOW_9PT"
  | "TEXT_LOW_CONTRAST"
  | "SLIDE_OVERDENSE"
  | "CHART_UNIT_UNSPECIFIED"
  | "CHART_LEGEND_MISSING"
  | "TABLE_TOO_MANY_COLUMNS"
  | "ELEMENT_UNSUPPORTED"
  | "ELEMENT_RASTER_ONLY";

export type TemplateV2QualitySeverity = "error" | "warning" | "info";

export interface TemplateV2QualityFinding {
  id: string;
  reasonCode: TemplateV2QualityReasonCode;
  severity: TemplateV2QualitySeverity;
  elementPath: ReadonlyArray<string | number>;
  details: Readonly<Record<string, string | number | boolean>>;
  safeFixAvailable: boolean;
}

export interface TemplateV2QualityInspection {
  sourceDigest: string;
  findings: ReadonlyArray<TemplateV2QualityFinding>;
}

export interface TemplateV2QualityPatch {
  reasonCode: TemplateV2QualityReasonCode;
  path: ReadonlyArray<string | number>;
  before: unknown;
  after: unknown;
}

export interface TemplateV2QualityFixPreview {
  id: string;
  findingId: string;
  sourceDigest: string;
  expectedRevision: number;
  idempotencyKey: string;
  patch: TemplateV2QualityPatch;
  status: "preview";
}

export interface TemplateV2QualityApplyResult {
  layouts: JsonRecord;
  revision: number;
  previewId: string;
  historyKey: string;
  autosave: {
    expected_revision: number;
    idempotency_key: string;
  };
}

export type TemplateV2QualityFailureCode =
  | "template_v2_quality_layouts_invalid"
  | "template_v2_quality_finding_not_found"
  | "template_v2_quality_fix_unavailable"
  | "template_v2_quality_inspection_stale"
  | "template_v2_quality_preview_stale"
  | "template_v2_quality_preview_tampered"
  | "template_v2_quality_revision_invalid"
  | "template_v2_quality_stale_revision"
  | "template_v2_quality_idempotency_key_invalid"
  | "template_v2_quality_idempotency_conflict";

export class TemplateV2QualityInspectorError extends Error {
  readonly code: TemplateV2QualityFailureCode;

  constructor(code: TemplateV2QualityFailureCode) {
    super(code);
    this.code = code;
    this.name = "TemplateV2QualityInspectorError";
  }
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, canonicalize(value[key])])
  );
}

function stableDigest(value: unknown): string {
  const encoded = JSON.stringify(canonicalize(value)) ?? "undefined";
  let hash = 0x811c9dc5;
  for (let index = 0; index < encoded.length; index += 1) {
    hash ^= encoded.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `quality-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function pathKey(path: ReadonlyArray<string | number>): string {
  return path
    .map((part) =>
      typeof part === "number"
        ? `#${part}`
        : `$${part.replaceAll("\\", "\\\\").replaceAll("/", "\\/")}`
    )
    .join("/");
}

function validateElement(element: unknown): asserts element is JsonRecord {
  if (!isRecord(element) || typeof element.type !== "string") {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_layouts_invalid"
    );
  }
  if (["flex", "grid", "group"].includes(element.type)) {
    if (!Array.isArray(element.children)) {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_layouts_invalid"
      );
    }
    element.children.forEach(validateElement);
  } else if (element.type === "container" && element.child !== null) {
    validateElement(element.child);
  }
}

function strictLayouts(layouts: JsonRecord): JsonRecord[] {
  if (!Array.isArray(layouts.layouts)) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_layouts_invalid"
    );
  }
  for (const layout of layouts.layouts) {
    if (
      !isRecord(layout) ||
      typeof layout.id !== "string" ||
      !Array.isArray(layout.components)
    ) {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_layouts_invalid"
      );
    }
    for (const component of layout.components) {
      if (
        !isRecord(component) ||
        typeof component.id !== "string" ||
        !Array.isArray(component.elements)
      ) {
        throw new TemplateV2QualityInspectorError(
          "template_v2_quality_layouts_invalid"
        );
      }
      component.elements.forEach(validateElement);
    }
  }
  return layouts.layouts as JsonRecord[];
}

function walkElements(
  elements: ReadonlyArray<JsonRecord>,
  rootPath: ReadonlyArray<string | number>
): Array<{ path: ReadonlyArray<string | number>; element: JsonRecord }> {
  const walked: Array<{
    path: ReadonlyArray<string | number>;
    element: JsonRecord;
  }> = [];
  elements.forEach((element, index) => {
    const path = [...rootPath, index];
    walked.push({ path, element });
    if (
      ["flex", "grid", "group"].includes(String(element.type)) &&
      Array.isArray(element.children)
    ) {
      walked.push(
        ...walkElements(element.children as JsonRecord[], [...path, "children"])
      );
    } else if (element.type === "container" && isRecord(element.child)) {
      walked.push(...walkElements([element.child], [...path, "child"]));
    }
  });
  return walked;
}

function fontEntries(
  element: JsonRecord
): Array<{ path: ReadonlyArray<string | number>; font: JsonRecord }> {
  const entries: Array<{
    path: ReadonlyArray<string | number>;
    font: JsonRecord;
  }> = [];
  if (isRecord(element.font)) entries.push({ path: ["font"], font: element.font });
  if (Array.isArray(element.runs)) {
    element.runs.forEach((run, index) => {
      if (isRecord(run) && isRecord(run.font)) {
        entries.push({ path: ["runs", index, "font"], font: run.font });
      }
    });
  }
  if (Array.isArray(element.items)) {
    element.items.forEach((item, itemIndex) => {
      if (!Array.isArray(item)) return;
      item.forEach((run, runIndex) => {
        if (isRecord(run) && isRecord(run.font)) {
          entries.push({
            path: ["items", itemIndex, runIndex, "font"],
            font: run.font,
          });
        }
      });
    });
  }
  for (const collectionName of ["columns", "rows"] as const) {
    const collection = element[collectionName];
    if (!Array.isArray(collection)) continue;
    const rows = collectionName === "columns" ? [collection] : collection;
    rows.forEach((row, rowIndex) => {
      if (!Array.isArray(row)) return;
      row.forEach((cell, cellIndex) => {
        if (!isRecord(cell)) return;
        const prefix: ReadonlyArray<string | number> =
          collectionName === "columns"
            ? ["columns", cellIndex]
            : ["rows", rowIndex, cellIndex];
        if (isRecord(cell.font)) {
          entries.push({ path: [...prefix, "font"], font: cell.font });
        }
        if (!Array.isArray(cell.runs)) return;
        cell.runs.forEach((run, runIndex) => {
          if (isRecord(run) && isRecord(run.font)) {
            entries.push({
              path: [...prefix, "runs", runIndex, "font"],
              font: run.font,
            });
          }
        });
      });
    });
  }
  return entries;
}

function textContent(element: JsonRecord): string {
  if (Array.isArray(element.runs)) {
    return element.runs
      .filter(isRecord)
      .map((run) => (typeof run.text === "string" ? run.text : ""))
      .join("");
  }
  if (Array.isArray(element.items)) {
    return element.items
      .map((item) =>
        Array.isArray(item)
          ? item
              .filter(isRecord)
              .map((run) => (typeof run.text === "string" ? run.text : ""))
              .join("")
          : ""
      )
      .join("\n");
  }
  return "";
}

function rgb(value: unknown): [number, number, number] | null {
  if (typeof value !== "string") return null;
  let normalized = value.trim();
  if (/^#[0-9a-f]{3}$/i.test(normalized)) {
    normalized = `#${normalized
      .slice(1)
      .split("")
      .map((character) => character.repeat(2))
      .join("")}`;
  }
  if (!/^#[0-9a-f]{6}$/i.test(normalized)) return null;
  return [1, 3, 5].map((offset) =>
    Number.parseInt(normalized.slice(offset, offset + 2), 16)
  ) as [number, number, number];
}

function luminance(color: [number, number, number]): number {
  const [red, green, blue] = color.map((channel) => {
    const value = channel / 255;
    return value <= 0.04045
      ? value / 12.92
      : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrast(foreground: unknown, background: unknown): number | null {
  const foregroundRgb = rgb(foreground);
  const backgroundRgb = rgb(background);
  if (!foregroundRgb || !backgroundRgb) return null;
  const values = [luminance(foregroundRgb), luminance(backgroundRgb)].sort(
    (left, right) => right - left
  );
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function contrastColor(background: unknown): "#000000" | "#FFFFFF" | null {
  const black = contrast("#000000", background);
  const white = contrast("#FFFFFF", background);
  if (black === null || white === null) return null;
  return black >= white ? "#000000" : "#FFFFFF";
}

type FindingDraft = Omit<TemplateV2QualityFinding, "id" | "safeFixAvailable">;

function finding(
  reasonCode: TemplateV2QualityReasonCode,
  severity: TemplateV2QualitySeverity,
  elementPath: ReadonlyArray<string | number>,
  details: Record<string, string | number | boolean> = {}
): FindingDraft {
  return { reasonCode, severity, elementPath, details };
}

function inspectElement(
  element: JsonRecord,
  path: ReadonlyArray<string | number>
): FindingDraft[] {
  const findings: FindingDraft[] = [];
  const type = String(element.type);

  if (["text", "text-list", "table"].includes(type)) {
    for (const entry of fontEntries(element)) {
      const size = entry.font.size;
      if (
        typeof size === "number" &&
        Number.isFinite(size) &&
        size < TEMPLATE_V2_QUALITY_MIN_FONT_SIZE
      ) {
        findings.push(
          finding("TEXT_BELOW_9PT", "warning", [...path, ...entry.path, "size"], {
            actual: size,
            minimum: TEMPLATE_V2_QUALITY_MIN_FONT_SIZE,
          })
        );
      }
    }
  }

  if (type === "text") {
    const text = textContent(element);
    const explicitOverflow =
      element.overflow === true ||
      (isRecord(element.text_fit) && element.text_fit.overflow === true);
    if (
      explicitOverflow ||
      (Number.isSafeInteger(element.max_length) &&
        text.length > Number(element.max_length))
    ) {
      findings.push(
        finding("TEXT_OVERFLOW", "error", path, {
          actual_length: text.length,
          maximum: Number.isSafeInteger(element.max_length)
            ? Number(element.max_length)
            : -1,
        })
      );
    }
    const background =
      element.background_color ??
      (isRecord(element.fill) ? element.fill.color : undefined);
    for (const entry of fontEntries(element)) {
      const ratio = contrast(entry.font.color, background);
      if (ratio !== null && ratio < TEMPLATE_V2_QUALITY_MIN_CONTRAST) {
        findings.push(
          finding(
            "TEXT_LOW_CONTRAST",
            "warning",
            [...path, ...entry.path, "color"],
            {
              background: String(background),
              ratio: Math.round(ratio * 1000) / 1000,
              required: TEMPLATE_V2_QUALITY_MIN_CONTRAST,
            }
          )
        );
      }
    }
  }

  if (type === "chart") {
    const series = Array.isArray(element.series) ? element.series : [];
    if (series.length > 1 && element.legend !== true) {
      findings.push(
        finding("CHART_LEGEND_MISSING", "warning", path, {
          series_count: series.length,
        })
      );
    }
    const hasValues = series.some(
      (entry) => isRecord(entry) && Array.isArray(entry.values) && entry.values.length
    );
    if (hasValues && !String(element.y_axis_title ?? "").trim()) {
      findings.push(finding("CHART_UNIT_UNSPECIFIED", "info", path));
    }
  }

  if (
    type === "table" &&
    Array.isArray(element.columns) &&
    element.columns.length > TEMPLATE_V2_QUALITY_MAX_TABLE_COLUMNS
  ) {
    findings.push(
      finding("TABLE_TOO_MANY_COLUMNS", "warning", path, {
        actual: element.columns.length,
        recommended_maximum: TEMPLATE_V2_QUALITY_MAX_TABLE_COLUMNS,
      })
    );
  }

  const compatibility = isRecord(element.compatibility)
    ? element.compatibility
    : null;
  const unsupportedReason =
    element.unsupported_reason ?? compatibility?.unsupported_reason;
  if (unsupportedReason) {
    findings.push(
      finding("ELEMENT_UNSUPPORTED", "error", path, {
        reason: String(unsupportedReason),
      })
    );
  }
  if (
    element.raster_only === true ||
    ["raster", "raster-only"].includes(String(compatibility?.render_mode ?? ""))
  ) {
    findings.push(finding("ELEMENT_RASTER_ONLY", "warning", path));
  }
  return findings;
}

const SAFE_FIX_CODES = new Set<TemplateV2QualityReasonCode>([
  "TEXT_BELOW_9PT",
  "TEXT_LOW_CONTRAST",
  "CHART_LEGEND_MISSING",
]);

export function inspectTemplateV2Quality(
  layouts: JsonRecord
): TemplateV2QualityInspection {
  const strict = strictLayouts(layouts);
  const sourceDigest = stableDigest(layouts);
  const drafts: FindingDraft[] = [];
  strict.forEach((layout, layoutIndex) => {
    let leafCount = 0;
    (layout.components as JsonRecord[]).forEach((component, componentIndex) => {
      const rootPath = [
        "layouts",
        layoutIndex,
        "components",
        componentIndex,
        "elements",
      ] as const;
      for (const walked of walkElements(
        component.elements as JsonRecord[],
        rootPath
      )) {
        if (
          !["container", "flex", "grid", "group"].includes(
            String(walked.element.type)
          )
        ) {
          leafCount += 1;
        }
        drafts.push(...inspectElement(walked.element, walked.path));
      }
    });
    if (leafCount > TEMPLATE_V2_QUALITY_MAX_LEAVES) {
      drafts.push(
        finding("SLIDE_OVERDENSE", "warning", ["layouts", layoutIndex], {
          actual: leafCount,
          recommended_maximum: TEMPLATE_V2_QUALITY_MAX_LEAVES,
        })
      );
    }
  });
  drafts.sort((left, right) => {
    const pathOrder = pathKey(left.elementPath).localeCompare(
      pathKey(right.elementPath)
    );
    return pathOrder || left.reasonCode.localeCompare(right.reasonCode);
  });
  return {
    sourceDigest,
    findings: drafts.map((draft) => ({
      ...draft,
      id: `${sourceDigest}:${draft.reasonCode}:${pathKey(draft.elementPath)}`,
      safeFixAvailable: SAFE_FIX_CODES.has(draft.reasonCode),
    })),
  };
}

function readPath(
  source: unknown,
  path: ReadonlyArray<string | number>
): unknown {
  let value = source;
  for (const part of path) {
    if (typeof part === "number") {
      if (!Array.isArray(value) || part < 0 || part >= value.length) {
        throw new TemplateV2QualityInspectorError(
          "template_v2_quality_preview_tampered"
        );
      }
      value = value[part];
    } else {
      if (!isRecord(value) || !(part in value)) {
        throw new TemplateV2QualityInspectorError(
          "template_v2_quality_preview_tampered"
        );
      }
      value = value[part];
    }
  }
  return value;
}

function writePath(
  source: JsonRecord,
  patch: TemplateV2QualityPatch
): void {
  if (!patch.path.length) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_preview_tampered"
    );
  }
  let parent: unknown = source;
  for (const part of patch.path.slice(0, -1)) {
    if (typeof part === "number") {
      if (!Array.isArray(parent) || part < 0 || part >= parent.length) {
        throw new TemplateV2QualityInspectorError(
          "template_v2_quality_preview_tampered"
        );
      }
      parent = parent[part];
    } else {
      if (!isRecord(parent) || !(part in parent)) {
        throw new TemplateV2QualityInspectorError(
          "template_v2_quality_preview_tampered"
        );
      }
      parent = parent[part];
    }
  }
  const field = patch.path.at(-1);
  if (typeof field === "number") {
    if (!Array.isArray(parent) || field < 0 || field >= parent.length) {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_preview_tampered"
      );
    }
    if (stableDigest(parent[field]) !== stableDigest(patch.before)) {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_preview_tampered"
      );
    }
    parent[field] = clone(patch.after);
  } else {
    if (!isRecord(parent) || typeof field !== "string") {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_preview_tampered"
      );
    }
    const current = parent[field];
    if (stableDigest(current) !== stableDigest(patch.before)) {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_preview_tampered"
      );
    }
    parent[field] = clone(patch.after);
  }
}

function requireIdempotencyKey(value: string): void {
  if (
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(value) ||
    value.includes("..")
  ) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_idempotency_key_invalid"
    );
  }
}

export function previewTemplateV2QualityFix({
  layouts,
  inspection,
  findingId,
  expectedRevision,
  idempotencyKey,
}: {
  layouts: JsonRecord;
  inspection: TemplateV2QualityInspection;
  findingId: string;
  expectedRevision: number;
  idempotencyKey: string;
}): TemplateV2QualityFixPreview {
  strictLayouts(layouts);
  requireIdempotencyKey(idempotencyKey);
  if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 1) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_revision_invalid"
    );
  }
  if (stableDigest(layouts) !== inspection.sourceDigest) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_inspection_stale"
    );
  }
  const finding = inspection.findings.find((entry) => entry.id === findingId);
  if (!finding) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_finding_not_found"
    );
  }
  if (!finding.safeFixAvailable) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_fix_unavailable"
    );
  }

  let patch: TemplateV2QualityPatch;
  if (finding.reasonCode === "TEXT_BELOW_9PT") {
    patch = {
      reasonCode: finding.reasonCode,
      path: finding.elementPath,
      before: clone(readPath(layouts, finding.elementPath)),
      after: TEMPLATE_V2_QUALITY_MIN_FONT_SIZE,
    };
  } else if (finding.reasonCode === "TEXT_LOW_CONTRAST") {
    const after = contrastColor(finding.details.background);
    if (!after) {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_fix_unavailable"
      );
    }
    patch = {
      reasonCode: finding.reasonCode,
      path: finding.elementPath,
      before: clone(readPath(layouts, finding.elementPath)),
      after,
    };
  } else {
    const path = [...finding.elementPath, "legend"];
    const element = readPath(layouts, finding.elementPath);
    if (!isRecord(element)) {
      throw new TemplateV2QualityInspectorError(
        "template_v2_quality_preview_tampered"
      );
    }
    patch = {
      reasonCode: finding.reasonCode,
      path,
      before: clone(element.legend),
      after: true,
    };
  }

  const id = stableDigest({
    sourceDigest: inspection.sourceDigest,
    findingId,
    expectedRevision,
    idempotencyKey,
    patch,
  });
  return {
    id,
    findingId,
    sourceDigest: inspection.sourceDigest,
    expectedRevision,
    idempotencyKey,
    patch,
    status: "preview",
  };
}

export function applyTemplateV2QualityFix({
  layouts,
  preview,
  expectedRevision,
  currentRevision,
  idempotencyKey,
}: {
  layouts: JsonRecord;
  preview: TemplateV2QualityFixPreview;
  expectedRevision: number;
  currentRevision: number;
  idempotencyKey: string;
}): TemplateV2QualityApplyResult {
  strictLayouts(layouts);
  requireIdempotencyKey(idempotencyKey);
  if (
    !Number.isSafeInteger(currentRevision) ||
    currentRevision < 1 ||
    !Number.isSafeInteger(expectedRevision) ||
    expectedRevision < 1
  ) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_revision_invalid"
    );
  }
  if (expectedRevision !== currentRevision) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_stale_revision"
    );
  }
  if (
    preview.idempotencyKey !== idempotencyKey ||
    preview.expectedRevision !== expectedRevision
  ) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_idempotency_conflict"
    );
  }
  if (stableDigest(layouts) !== preview.sourceDigest) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_preview_stale"
    );
  }
  const regeneratedPreview = previewTemplateV2QualityFix({
    layouts,
    inspection: inspectTemplateV2Quality(layouts),
    findingId: preview.findingId,
    expectedRevision,
    idempotencyKey,
  });
  if (stableDigest(preview) !== stableDigest(regeneratedPreview)) {
    throw new TemplateV2QualityInspectorError(
      "template_v2_quality_preview_tampered"
    );
  }
  const result = clone(layouts);
  writePath(result, preview.patch);
  strictLayouts(result);
  return {
    layouts: result,
    revision: currentRevision + 1,
    previewId: preview.id,
    historyKey: `quality-fix:${preview.id}`,
    autosave: {
      expected_revision: currentRevision,
      idempotency_key: idempotencyKey,
    },
  };
}
