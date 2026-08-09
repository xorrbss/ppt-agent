/**
 * Stage 3 (map to schema) — the frame/identity fields every adapted element
 * carries: position, size, rotation, opacity, component identity, design
 * variables and flow layout hints.
 */
import {
  EDITOR_STAGE_HEIGHT,
  EDITOR_STAGE_WIDTH,
  type DesignVariable,
  type LayoutItem,
  type Shadow,
} from "@/components/slide-editor/types";
import {
  MIN_ELEMENT_SIZE,
  asRecord,
  clamp,
  clampInteger,
  clampOptional,
  isRecord,
  readArray,
  readDecorative,
  readLayoutAlignment,
  readNumber,
  readRecord,
  readString,
  readValue,
  round,
  stripNullish,
  truncateString,
  type AdaptedPosition,
  type AdaptedSize,
  type UnknownRecord,
} from "@/components/slide-editor/importing/template-v2/values";
import { adaptShadow } from "@/components/slide-editor/importing/template-v2/styles";

export type AdaptedBaseElement = {
  decorative?: boolean | null;
  position?: AdaptedPosition | null;
  size?: AdaptedSize | null;
  rotation?: number | null;
  opacity?: number | null;
  shadow?: Shadow | null;
  component_id?: string | null;
  component_instance_id?: string | null;
  component_description?: string | null;
  component_slot?: string | null;
  design_variables?: DesignVariable[] | null;
  layout?: LayoutItem | null;
};

export type AdaptedRequiredBaseElement = AdaptedBaseElement & {
  position: AdaptedPosition;
  size: AdaptedSize;
};

export function baseElement(
  raw: UnknownRecord,
  options: { requireFrame?: boolean } = {},
): AdaptedBaseElement {
  const base: AdaptedBaseElement = {};
  const position = adaptPosition(readRecord(raw, "position"));
  const size = adaptSize(readRecord(raw, "size"));
  const componentSlot =
    readString(readValue(raw, "component_slot")) ??
    readString(raw.name);

  const decorative = readDecorative(raw);
  if (decorative != null) base.decorative = decorative;
  if (position) base.position = position;
  if (size) base.size = size;
  if (options.requireFrame && !position) base.position = { x: 0, y: 0 };
  if (options.requireFrame && !size) base.size = { width: EDITOR_STAGE_WIDTH, height: EDITOR_STAGE_HEIGHT };
  if (options.requireFrame && !readRecord(raw, "layout")) {
    base.layout = { grow: 1, shrink: 1 };
  }
  if (readNumber(raw, "rotation") != null) {
    base.rotation = clamp(readNumber(raw, "rotation") ?? 0, -360, 360);
  }
  if (readNumber(raw, "opacity") != null) {
    base.opacity = clamp(readNumber(raw, "opacity") ?? 1, 0, 1);
  }
  if (componentSlot) base.component_slot = truncateString(componentSlot, 120);
  const designVariables = adaptDesignVariables(
    readArray(raw, "design_variables"),
  );
  if (designVariables.length > 0) base.design_variables = designVariables;
  if (readString(readValue(raw, "component_id"))) {
    base.component_id = truncateString(
      readString(readValue(raw, "component_id")) ?? "",
      120,
    );
  }
  if (readString(readValue(raw, "component_instance_id"))) {
    base.component_instance_id = truncateString(
      readString(readValue(raw, "component_instance_id")) ?? "",
      160,
    );
  }
  if (readString(readValue(raw, "component_description"))) {
    base.component_description = truncateString(
      readString(readValue(raw, "component_description")) ?? "",
      600,
    );
  }

  const shadow = adaptShadow(readRecord(raw, "shadow"));
  if (shadow) base.shadow = shadow;

  const layout = adaptLayoutItem(readRecord(raw, "layout"));
  if (layout) base.layout = { ...(base.layout ?? {}), ...layout };

  return base;
}

export function requiredBaseElement(
  raw: UnknownRecord,
): AdaptedRequiredBaseElement {
  return baseElement(raw, { requireFrame: true }) as AdaptedRequiredBaseElement;
}

function adaptDesignVariables(values: unknown[]): DesignVariable[] {
  return values
    .map((value): DesignVariable | null => {
      const raw = asRecord(value);
      if (!raw) return null;

      const name = truncateString(readString(raw.name) ?? "", 120);
      const type = truncateString(readString(raw.type) ?? "", 40);
      const options: unknown[] = readArray(raw, "options")
        .map(toJsonValue)
        .filter((option) => option !== undefined);
      const effect = readArray(raw, "effect")
        .map((item) => {
          const rawEffect = asRecord(item);
          if (!rawEffect) return null;
          const path = truncateString(
            readString(readValue(rawEffect, "path")) ?? "",
            240,
          );
          const expression = truncateString(
            readString(readValue(rawEffect, "effect")) ?? "",
            120,
          );
          return path && expression ? { path, effect: expression } : null;
        })
        .filter((item): item is DesignVariable["effect"][number] =>
          Boolean(item),
        );

      if (!name || options.length === 0 || effect.length === 0) return null;
      return {
        name,
        ...(type ? { type } : {}),
        options,
        effect,
      } satisfies DesignVariable;
    })
    .filter((item): item is DesignVariable => Boolean(item));
}

function toJsonValue(value: unknown): unknown {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }

  if (Array.isArray(value)) {
    return value.map(toJsonValue).filter((item) => item !== undefined);
  }

  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value)
        .map(([key, item]) => [key, toJsonValue(item)] as const)
        .filter(([, item]) => item !== undefined),
    );
  }

  return undefined;
}

function adaptPosition(value: UnknownRecord | null): { x: number; y: number } | null {
  if (!value) return null;
  return {
    x: round(readNumber(value, "x") ?? 0),
    y: round(readNumber(value, "y") ?? 0),
  };
}

function adaptSize(value: UnknownRecord | null): { width: number; height: number } | null {
  if (!value) return null;
  return {
    width: clamp(round(readNumber(value, "width") ?? MIN_ELEMENT_SIZE), MIN_ELEMENT_SIZE, EDITOR_STAGE_WIDTH),
    height: clamp(round(readNumber(value, "height") ?? MIN_ELEMENT_SIZE), MIN_ELEMENT_SIZE, EDITOR_STAGE_HEIGHT),
  };
}

function adaptLayoutItem(value: UnknownRecord | null): LayoutItem | null {
  if (!value) return null;
  return stripNullish({
    grow: clampOptional(readNumber(value, "grow"), 0, 12),
    shrink: clampOptional(readNumber(value, "shrink"), 0, 12),
    basis: readNumber(value, "basis"),
    min_width: readNumber(value, "min_width"),
    max_width: readNumber(value, "max_width"),
    min_height: readNumber(value, "min_height"),
    max_height: readNumber(value, "max_height"),
    column_span: clampInteger(readNumber(value, "column_span"), 1, 12),
    row_span: clampInteger(readNumber(value, "row_span"), 1, 12),
    align_self: readLayoutAlignment(value, "align_self"),
  });
}
