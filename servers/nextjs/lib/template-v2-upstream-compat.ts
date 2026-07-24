import type { JsonRecord } from "./template-v2-studio.ts";

export type TemplateV2LayoutsShape = "array" | "envelope" | "nested-envelope";

export interface TemplateV2LayoutsCompatibilityDocument {
  shape: TemplateV2LayoutsShape;
  source: unknown;
  studioLayouts: JsonRecord;
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function invalidLayouts(): never {
  throw new Error("template_v2_upstream_layouts_invalid");
}

/**
 * Adapts every layouts envelope accepted by the pinned upstream Template V2
 * API to the local Studio envelope without discarding extension fields.
 *
 * The immutable source is retained separately so serialization can restore
 * the exact upstream wire shape after the Studio edits the canonical layouts
 * array. Session-only lock/history state is deliberately not part of this
 * document.
 */
export function adaptUpstreamTemplateV2LayoutsToStudio(
  value: unknown
): TemplateV2LayoutsCompatibilityDocument {
  if (Array.isArray(value)) {
    const layouts = records(value);
    if (layouts.length !== value.length) invalidLayouts();
    return {
      shape: "array",
      source: clone(value),
      studioLayouts: { layouts: clone(layouts) },
    };
  }

  if (!isRecord(value)) invalidLayouts();

  if (Array.isArray(value.layouts)) {
    const layouts = records(value.layouts);
    if (layouts.length !== value.layouts.length) invalidLayouts();
    return {
      shape: "envelope",
      source: clone(value),
      studioLayouts: clone(value),
    };
  }

  if (isRecord(value.layouts) && Array.isArray(value.layouts.layouts)) {
    const layouts = records(value.layouts.layouts);
    if (layouts.length !== value.layouts.layouts.length) invalidLayouts();
    return {
      shape: "nested-envelope",
      source: clone(value),
      studioLayouts: clone(value.layouts),
    };
  }

  invalidLayouts();
}

/**
 * Restores the original upstream wire shape while merging the edited Studio
 * envelope. Unknown fields at the top level, envelope level, layout level and
 * element level remain intact unless the Studio explicitly changed them.
 */
export function serializeStudioLayoutsForUpstream(
  document: TemplateV2LayoutsCompatibilityDocument,
  studioLayouts: JsonRecord = document.studioLayouts
): unknown {
  const editedLayouts = records(studioLayouts.layouts);
  if (
    !Array.isArray(studioLayouts.layouts) ||
    editedLayouts.length !== studioLayouts.layouts.length
  ) {
    invalidLayouts();
  }

  if (document.shape === "array") {
    return clone(editedLayouts);
  }

  if (document.shape === "envelope") {
    const source = isRecord(document.source) ? clone(document.source) : {};
    return {
      ...source,
      ...clone(studioLayouts),
      layouts: clone(editedLayouts),
    };
  }

  if (!isRecord(document.source)) invalidLayouts();
  const source = clone(document.source);
  const sourceEnvelope = isRecord(source.layouts) ? source.layouts : {};
  return {
    ...source,
    layouts: {
      ...sourceEnvelope,
      ...clone(studioLayouts),
      layouts: clone(editedLayouts),
    },
  };
}

/**
 * Upstream generated presentations may persist each native layout in
 * `slide.ui`. Sorting by the generated slide index makes the conversion stable
 * and keeps every unknown slide-ui field available to the Studio.
 */
export function adaptGeneratedTemplateV2UiToStudio(
  presentation: unknown
): TemplateV2LayoutsCompatibilityDocument {
  if (!isRecord(presentation) || !Array.isArray(presentation.slides)) {
    invalidLayouts();
  }

  const layouts = presentation.slides
    .filter(isRecord)
    .map((slide, sourceIndex) => ({
      sourceIndex,
      index:
        typeof slide.index === "number" && Number.isFinite(slide.index)
          ? slide.index
          : sourceIndex,
      ui: slide.ui,
    }))
    .filter((entry): entry is typeof entry & { ui: JsonRecord } =>
      isRecord(entry.ui)
    )
    .sort(
      (left, right) =>
        left.index - right.index || left.sourceIndex - right.sourceIndex
    )
    .map((entry) => clone(entry.ui));

  if (layouts.length === 0) invalidLayouts();
  return {
    shape: "envelope",
    source: { layouts: clone(layouts) },
    studioLayouts: { layouts },
  };
}
