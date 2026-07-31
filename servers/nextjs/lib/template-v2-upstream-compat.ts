import type { JsonRecord } from "./template-v2-studio.ts";

export type TemplateV2LayoutsShape = "array" | "envelope" | "nested-envelope";

interface TemplateV2PinnedNormalization {
  layoutId: string;
  componentId: string;
  elementIndex: number;
  upstreamType: "rectangle";
  studioType: "container";
}

export interface TemplateV2LayoutsCompatibilityDocument {
  shape: TemplateV2LayoutsShape;
  source: unknown;
  studioLayouts: JsonRecord;
  normalizations?: TemplateV2PinnedNormalization[];
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

function hasExactKeys(value: JsonRecord, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === expected[index])
  );
}

function isPinnedDesignVariableState(
  component: JsonRecord,
  expectedElementType: "rectangle" | "container"
): boolean {
  if (
    component.id !== "metric_card" ||
    component.description !==
      "Reusable metric card with scalable typography." ||
    !isRecord(component.position) ||
    !hasExactKeys(component.position, ["x", "y"]) ||
    component.position.x !== 0 ||
    component.position.y !== 0 ||
    !isRecord(component.size) ||
    !hasExactKeys(component.size, ["width", "height"]) ||
    component.size.width !== 200 ||
    component.size.height !== 100 ||
    !Array.isArray(component.design_variables) ||
    component.design_variables.length !== 1 ||
    !Array.isArray(component.elements) ||
    component.elements.length !== 2
  ) {
    return false;
  }

  const variable = component.design_variables[0];
  const rectangle = component.elements[0];
  const text = component.elements[1];
  if (
    !isRecord(variable) ||
    !hasExactKeys(variable, ["name", "type", "options", "effect"]) ||
    variable.name !== "scale_variant" ||
    variable.type !== "object" ||
    !Array.isArray(variable.options) ||
    variable.options.length !== 1 ||
    !Array.isArray(variable.effect) ||
    variable.effect.length !== 2 ||
    !isRecord(rectangle) ||
    !hasExactKeys(rectangle, ["type", "position", "size"]) ||
    rectangle.type !== expectedElementType ||
    !isRecord(rectangle.position) ||
    !hasExactKeys(rectangle.position, ["x", "y"]) ||
    rectangle.position.x !== 0 ||
    rectangle.position.y !== 0 ||
    !isRecord(rectangle.size) ||
    !hasExactKeys(rectangle.size, ["width", "height"]) ||
    rectangle.size.height !== 100 ||
    !isRecord(text) ||
    !hasExactKeys(text, [
      "type",
      "position",
      "size",
      "fixed",
      "name",
      "min_length",
      "max_length",
      "runs",
    ]) ||
    text.type !== "text" ||
    !isRecord(text.position) ||
    !hasExactKeys(text.position, ["x", "y"]) ||
    text.position.x !== 20 ||
    text.position.y !== 20 ||
    !isRecord(text.size) ||
    !hasExactKeys(text.size, ["width", "height"]) ||
    text.size.width !== 160 ||
    text.size.height !== 40 ||
    text.fixed !== false ||
    text.name !== "metric" ||
    text.min_length !== 10 ||
    text.max_length !== 20 ||
    !Array.isArray(text.runs) ||
    text.runs.length !== 1
  ) {
    return false;
  }

  const option = variable.options[0];
  const widthEffect = variable.effect[0];
  const fontSizeEffect = variable.effect[1];
  const run = text.runs[0];
  const font = isRecord(run) ? run.font : null;
  if (
    !isRecord(option) ||
    !hasExactKeys(option, ["width", "font_size"]) ||
    option.width !== 240 ||
    option.font_size !== 28 ||
    !isRecord(widthEffect) ||
    !hasExactKeys(widthEffect, ["source", "target"]) ||
    widthEffect.source !== "$.width" ||
    widthEffect.target !== "elements.0.size.width" ||
    !isRecord(fontSizeEffect) ||
    !hasExactKeys(fontSizeEffect, ["source", "target"]) ||
    fontSizeEffect.source !== "$.font_size" ||
    fontSizeEffect.target !== "elements.1.runs.0.font.size" ||
    !isRecord(run) ||
    !hasExactKeys(run, ["text", "font"]) ||
    run.text !== "Metric" ||
    !isRecord(font) ||
    !hasExactKeys(font, ["size"])
  ) {
    return false;
  }

  const width = rectangle.size.width;
  const fontSize = font.size;
  return (
    (width === 200 && fontSize === 20) ||
    (width === 240 && fontSize === 28)
  );
}

function normalizePinnedDesignVariableRectangles(
  studioLayouts: JsonRecord
): TemplateV2PinnedNormalization[] {
  const normalizations: TemplateV2PinnedNormalization[] = [];
  records(studioLayouts.layouts).forEach((layout) => {
    records(layout.components).forEach((component) => {
      if (typeof layout.id !== "string" || typeof component.id !== "string") {
        return;
      }
      if (!isPinnedDesignVariableState(component, "rectangle")) return;
      const element = records(component.elements)[0];
      if (!element) return;
      element.type = "container";
      normalizations.push({
        layoutId: layout.id,
        componentId: component.id,
        elementIndex: 0,
        upstreamType: "rectangle",
        studioType: "container",
      });
    });
  });
  return normalizations;
}

function denormalizePinnedDesignVariableRectangles(
  studioLayouts: JsonRecord,
  normalizations: readonly TemplateV2PinnedNormalization[]
): void {
  const layouts = records(studioLayouts.layouts);
  normalizations.forEach((normalization) => {
    const matchingLayouts = layouts.filter(
      (layout) => layout.id === normalization.layoutId
    );
    if (matchingLayouts.length !== 1) {
      throw new Error("template_v2_upstream_normalization_invalid");
    }
    const matchingComponents = records(matchingLayouts[0]!.components).filter(
      (component) => component.id === normalization.componentId
    );
    if (matchingComponents.length !== 1) {
      throw new Error("template_v2_upstream_normalization_invalid");
    }
    const component = matchingComponents[0];
    const element = component
      ? records(component.elements)[normalization.elementIndex]
      : undefined;
    if (
      !component ||
      !isPinnedDesignVariableState(component, normalization.studioType) ||
      !element ||
      element.type !== normalization.studioType
    ) {
      throw new Error("template_v2_upstream_normalization_invalid");
    }
    element.type = normalization.upstreamType;
  });
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
    const studioLayouts = { layouts: clone(layouts) };
    return {
      shape: "array",
      source: clone(value),
      studioLayouts,
      normalizations:
        normalizePinnedDesignVariableRectangles(studioLayouts),
    };
  }

  if (!isRecord(value)) invalidLayouts();

  if (Array.isArray(value.layouts)) {
    const layouts = records(value.layouts);
    if (layouts.length !== value.layouts.length) invalidLayouts();
    const studioLayouts = clone(value);
    return {
      shape: "envelope",
      source: clone(value),
      studioLayouts,
      normalizations:
        normalizePinnedDesignVariableRectangles(studioLayouts),
    };
  }

  if (isRecord(value.layouts) && Array.isArray(value.layouts.layouts)) {
    const layouts = records(value.layouts.layouts);
    if (layouts.length !== value.layouts.layouts.length) invalidLayouts();
    const studioLayouts = clone(value.layouts);
    return {
      shape: "nested-envelope",
      source: clone(value),
      studioLayouts,
      normalizations:
        normalizePinnedDesignVariableRectangles(studioLayouts),
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
  const wireStudioLayouts = clone(studioLayouts);
  denormalizePinnedDesignVariableRectangles(
    wireStudioLayouts,
    document.normalizations ?? []
  );
  const editedLayouts = records(wireStudioLayouts.layouts);
  if (
    !Array.isArray(wireStudioLayouts.layouts) ||
    editedLayouts.length !== wireStudioLayouts.layouts.length
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
      ...clone(wireStudioLayouts),
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
      ...clone(wireStudioLayouts),
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
