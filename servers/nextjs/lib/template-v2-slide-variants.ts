export type JsonRecord = Record<string, unknown>;
export type TemplateV2VariantKind =
  | "data_focused"
  | "image_focused"
  | "executive_summary";

export interface TemplateV2VariantPatch {
  path: Array<string | number>;
  before: unknown;
  after: unknown;
}

export interface TemplateV2SlideVariantCandidate {
  kind: TemplateV2VariantKind;
  label: string;
  patches: TemplateV2VariantPatch[];
  semanticDigest: string;
  renderDigest: string;
}

export interface TemplateV2SlideVariantPreview {
  id: string;
  sourceDigest: string;
  sourceRevision: number;
  layoutId: string;
  sourceLayoutDigest: string;
  candidates: TemplateV2SlideVariantCandidate[];
}

export interface TemplateV2SlideVariantJournalEntry {
  reason: `slide-variant:${TemplateV2VariantKind}`;
  sourceRevision: number;
  layoutId: string;
  beforeLayout: JsonRecord;
  appliedDigest: string;
}

export type TemplateV2VariantResult<T> =
  | { ok: true; value: T }
  | { ok: false; code: string };

const VISUAL_FIELDS = new Set([
  "x",
  "y",
  "width",
  "height",
  "size",
  "bold",
  "italic",
  "color",
  "opacity",
  "fit",
  "focus_x",
  "focus_y",
  "crop_scale",
  "legend",
  "x_axis",
  "y_axis",
  "x_axis_grid",
  "y_axis_grid",
  "data_labels",
]);

const SEMANTIC_IGNORED_FIELDS = new Set([
  ...VISUAL_FIELDS,
  "position",
  "font",
  "fill",
  "stroke",
  "shadow",
  "alignment",
  "rotation",
  "border_radius",
]);

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/**
 * Stable, browser-safe 128-bit display digest. The authoritative backend
 * contract recomputes SHA-256; this digest only binds a local preview to the
 * immutable Studio snapshot that produced it.
 */
export function templateV2VariantDigest(value: unknown): string {
  const source = canonical(value);
  const seeds = [0x811c9dc5, 0x9e3779b9, 0x85ebca6b, 0xc2b2ae35];
  return seeds
    .map((seed) => {
      let hash = seed >>> 0;
      for (let index = 0; index < source.length; index += 1) {
        hash ^= source.charCodeAt(index);
        hash = Math.imul(hash, 0x01000193) >>> 0;
      }
      return hash.toString(16).padStart(8, "0");
    })
    .join("");
}

function semanticProjection(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(semanticProjection);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !SEMANTIC_IGNORED_FIELDS.has(key))
      .map(([key, item]) => [key, semanticProjection(item)])
  );
}

function layoutList(layouts: JsonRecord): JsonRecord[] | null {
  if (!Array.isArray(layouts.layouts)) return null;
  const values = layouts.layouts;
  return values.every(isRecord) ? values : null;
}

function readPath(source: unknown, path: ReadonlyArray<string | number>): unknown {
  let value = source;
  for (const part of path) {
    if (typeof part === "number") {
      if (!Array.isArray(value) || part < 0 || part >= value.length) {
        throw new Error("template_v2_variant_patch_path_invalid");
      }
      value = value[part];
    } else {
      if (!isRecord(value) || !(part in value)) {
        throw new Error("template_v2_variant_patch_path_invalid");
      }
      value = value[part];
    }
  }
  return value;
}

function writePath(
  source: unknown,
  path: ReadonlyArray<string | number>,
  value: unknown
): void {
  const parent = readPath(source, path.slice(0, -1));
  const field = path.at(-1);
  if (typeof field === "number" && Array.isArray(parent)) {
    if (field < 0 || field >= parent.length) {
      throw new Error("template_v2_variant_patch_path_invalid");
    }
    parent[field] = clone(value);
    return;
  }
  if (typeof field === "string" && isRecord(parent) && field in parent) {
    parent[field] = clone(value);
    return;
  }
  throw new Error("template_v2_variant_patch_path_invalid");
}

function validVisualPath(path: ReadonlyArray<string | number>): boolean {
  if (
    path.length < 5 ||
    path[0] !== "components" ||
    typeof path[1] !== "number" ||
    path[2] !== "elements" ||
    typeof path[3] !== "number"
  ) {
    return false;
  }
  const tail = path.slice(4);
  if (tail.length === 1) return VISUAL_FIELDS.has(String(tail[0]));
  return (
    tail.length === 2 &&
    ((tail[0] === "position" && (tail[1] === "x" || tail[1] === "y")) ||
      (tail[0] === "size" &&
        (tail[1] === "width" || tail[1] === "height")) ||
      (tail[0] === "font" &&
        ["size", "bold", "italic", "color"].includes(String(tail[1]))) ||
      (tail[0] === "fill" &&
        (tail[1] === "color" || tail[1] === "opacity")))
  );
}

function directElements(
  layout: JsonRecord
): Array<{ componentIndex: number; elementIndex: number; element: JsonRecord }> {
  if (!Array.isArray(layout.components)) return [];
  return layout.components.flatMap((component, componentIndex) => {
    if (!isRecord(component) || !Array.isArray(component.elements)) return [];
    return component.elements.flatMap((element, elementIndex) =>
      isRecord(element) ? [{ componentIndex, elementIndex, element }] : []
    );
  });
}

function patchFor(
  entry: ReturnType<typeof directElements>[number],
  tail: Array<string | number>,
  after: unknown
): TemplateV2VariantPatch | null {
  const path = [
    "components",
    entry.componentIndex,
    "elements",
    entry.elementIndex,
    ...tail,
  ];
  try {
    const before = readPath(entry.element, tail);
    if (canonical(before) === canonical(after)) return null;
    return { path, before: clone(before), after: clone(after) };
  } catch {
    return null;
  }
}

function defaultCandidatePatches(
  layout: JsonRecord
): Array<{
  kind: TemplateV2VariantKind;
  label: string;
  patches: TemplateV2VariantPatch[];
}> {
  const elements = directElements(layout);
  const chart = elements.find(({ element }) => element.type === "chart");
  const image = elements.find(({ element }) => element.type === "image");
  const text = elements.find(
    ({ element }) => element.type === "text" && isRecord(element.font)
  );
  const candidates: Array<{
    kind: TemplateV2VariantKind;
    label: string;
    patches: TemplateV2VariantPatch[];
  }> = [];

  if (chart) {
    const patch =
      patchFor(chart, ["legend"], chart.element.legend !== true) ??
      patchFor(chart, ["data_labels"], chart.element.data_labels !== true) ??
      patchFor(chart, ["y_axis_grid"], chart.element.y_axis_grid !== true);
    if (patch) {
      candidates.push({
        kind: "data_focused",
        label: "Data focused",
        patches: [patch],
      });
    }
  }
  if (image) {
    const currentFit = image.element.fit;
    const patch =
      currentFit === "contain"
        ? patchFor(image, ["fit"], "cover")
        : currentFit === "cover"
          ? patchFor(image, ["fit"], "contain")
          : null;
    if (patch) {
      candidates.push({
        kind: "image_focused",
        label: "Image focused",
        patches: [patch],
      });
    }
  }
  if (text && isRecord(text.element.font)) {
    const patch = patchFor(
      text,
      ["font", "bold"],
      text.element.font.bold !== true
    );
    if (patch) {
      candidates.push({
        kind: "executive_summary",
        label: "Executive summary",
        patches: [patch],
      });
    }
  }
  return candidates;
}

function applyPatches(
  layout: JsonRecord,
  patches: ReadonlyArray<TemplateV2VariantPatch>
): JsonRecord {
  const result = clone(layout);
  for (const patch of patches) {
    if (!validVisualPath(patch.path)) {
      throw new Error("template_v2_variant_patch_not_visual");
    }
    if (canonical(readPath(layout, patch.path)) !== canonical(patch.before)) {
      throw new Error("template_v2_variant_preview_tampered");
    }
    writePath(result, patch.path, patch.after);
  }
  return result;
}

export function previewTemplateV2SlideVariants({
  layouts,
  layoutId,
  sourceRevision,
}: {
  layouts: JsonRecord;
  layoutId: string;
  sourceRevision: number;
}): TemplateV2VariantResult<TemplateV2SlideVariantPreview> {
  if (!Number.isSafeInteger(sourceRevision) || sourceRevision < 1) {
    return { ok: false, code: "template_v2_variant_revision_invalid" };
  }
  const values = layoutList(layouts);
  const layoutIndex = values?.findIndex((layout) => layout.id === layoutId) ?? -1;
  if (!values || layoutIndex < 0) {
    return { ok: false, code: "template_v2_variant_layout_invalid" };
  }
  const sourceLayout = values[layoutIndex];
  const requests = defaultCandidatePatches(sourceLayout);
  if (requests.length < 2 || requests.length > 3) {
    return {
      ok: false,
      code: "template_v2_variant_candidate_count_invalid",
    };
  }
  try {
    const candidates = requests.map((request) => {
      const candidateLayout = applyPatches(sourceLayout, request.patches);
      return {
        ...request,
        semanticDigest: templateV2VariantDigest(
          semanticProjection(candidateLayout)
        ),
        renderDigest: templateV2VariantDigest(candidateLayout),
      };
    });
    const sourceDigest = templateV2VariantDigest(layouts);
    const previewValue = {
      sourceDigest,
      sourceRevision,
      layoutId,
      candidates: candidates.map(({ kind, renderDigest }) => ({
        kind,
        renderDigest,
      })),
    };
    return {
      ok: true,
      value: {
        id: templateV2VariantDigest(previewValue),
        sourceDigest,
        sourceRevision,
        layoutId,
        sourceLayoutDigest: templateV2VariantDigest(sourceLayout),
        candidates,
      },
    };
  } catch (error) {
    return {
      ok: false,
      code:
        error instanceof Error
          ? error.message
          : "template_v2_variant_preview_invalid",
    };
  }
}

export function applyTemplateV2SlideVariant({
  layouts,
  preview,
  selectedKind,
  expectedRevision,
  currentRevision,
}: {
  layouts: JsonRecord;
  preview: TemplateV2SlideVariantPreview;
  selectedKind: TemplateV2VariantKind;
  expectedRevision: number;
  currentRevision: number;
}): TemplateV2VariantResult<{
  layouts: JsonRecord;
  sourceDigest: string;
  appliedDigest: string;
  journalEntry: TemplateV2SlideVariantJournalEntry;
}> {
  if (
    expectedRevision !== currentRevision ||
    currentRevision !== preview.sourceRevision
  ) {
    return { ok: false, code: "template_v2_variant_stale_revision" };
  }
  if (templateV2VariantDigest(layouts) !== preview.sourceDigest) {
    return { ok: false, code: "template_v2_variant_preview_stale" };
  }
  const values = layoutList(layouts);
  const layoutIndex = values?.findIndex((layout) => layout.id === preview.layoutId) ?? -1;
  const candidate = preview.candidates.find(
    (item) => item.kind === selectedKind
  );
  if (!values || layoutIndex < 0 || !candidate) {
    return { ok: false, code: "template_v2_variant_selection_invalid" };
  }
  try {
    const result = clone(layouts);
    const resultLayouts = layoutList(result);
    if (!resultLayouts) {
      return { ok: false, code: "template_v2_variant_layout_invalid" };
    }
    const beforeLayout = clone(values[layoutIndex]);
    resultLayouts[layoutIndex] = applyPatches(
      values[layoutIndex],
      candidate.patches
    );
    const appliedDigest = templateV2VariantDigest(result);
    return {
      ok: true,
      value: {
        layouts: result,
        sourceDigest: preview.sourceDigest,
        appliedDigest,
        journalEntry: {
          reason: `slide-variant:${selectedKind}`,
          sourceRevision: currentRevision,
          layoutId: preview.layoutId,
          beforeLayout,
          appliedDigest,
        },
      },
    };
  } catch (error) {
    return {
      ok: false,
      code:
        error instanceof Error
          ? error.message
          : "template_v2_variant_apply_invalid",
    };
  }
}

export function restoreTemplateV2SlideVariant({
  layouts,
  journalEntry,
  expectedRevision,
  currentRevision,
}: {
  layouts: JsonRecord;
  journalEntry: TemplateV2SlideVariantJournalEntry;
  expectedRevision: number;
  currentRevision: number;
}): TemplateV2VariantResult<{
  layouts: JsonRecord;
  sourceDigest: string;
}> {
  if (
    expectedRevision !== currentRevision ||
    ![journalEntry.sourceRevision, journalEntry.sourceRevision + 1].includes(
      currentRevision
    )
  ) {
    return { ok: false, code: "template_v2_variant_stale_revision" };
  }
  if (templateV2VariantDigest(layouts) !== journalEntry.appliedDigest) {
    return {
      ok: false,
      code: "template_v2_variant_restore_source_stale",
    };
  }
  const values = layoutList(layouts);
  const layoutIndex =
    values?.findIndex((layout) => layout.id === journalEntry.layoutId) ?? -1;
  if (!values || layoutIndex < 0) {
    return { ok: false, code: "template_v2_variant_journal_invalid" };
  }
  const result = clone(layouts);
  const resultLayouts = layoutList(result);
  if (!resultLayouts) {
    return { ok: false, code: "template_v2_variant_layout_invalid" };
  }
  resultLayouts[layoutIndex] = clone(journalEntry.beforeLayout);
  return {
    ok: true,
    value: {
      layouts: result,
      sourceDigest: journalEntry.appliedDigest,
    },
  };
}

export function cancelTemplateV2SlideVariants(
  preview: TemplateV2SlideVariantPreview
): { previewId: string; status: "cancelled" } {
  return { previewId: preview.id, status: "cancelled" };
}
