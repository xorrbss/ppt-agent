import {
  validateChartContract,
  validateTableContract,
} from "./template-v2-chart-table-contract.ts";
import { validateTemplateV2InfographicContract } from "./template-v2-infographic-contract.ts";
import { isTemplateV2SafeColor } from "./template-v2-render-plan.mjs";
import { planStudioElement } from "./template-v2-studio-plan.ts";
import { projectTemplateV2ElementForRenderPlan } from "./template-v2-vector.ts";

export type TemplateV2ToolbarRecord = Record<string, unknown>;

export type TemplateV2ToolbarRoute =
  | { kind: "none" }
  | { kind: "multi"; count: number }
  | {
      kind: "image" | "icon" | "text" | "text-list";
      element: TemplateV2ToolbarRecord;
    }
  | {
      kind: "vector" | "chart" | "table" | "infographic";
      element: TemplateV2ToolbarRecord;
    }
  | {
      kind: "unsupported";
      reason:
        | "missing-element"
        | "design-variables"
        | "element-type"
        | "invalid-contract";
      element: TemplateV2ToolbarRecord | null;
    };

export type TemplateV2ToolbarBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type TemplateV2ToolbarBounds = {
  left: number;
  top: number;
  right: number;
  bottom: number;
};

export type TemplateV2ToolbarPosition = {
  left: number;
  top: number;
  placement: "above" | "below" | "fallback";
  visible: boolean;
};

function isRecord(value: unknown): value is TemplateV2ToolbarRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/**
 * Fail closed around design_variables. Those controls intentionally stay in the
 * Inspector until their strict slide.ui boundary and export semantics are safe.
 */
function hasUnsafeDesignVariables(element: TemplateV2ToolbarRecord): boolean {
  if (!Object.prototype.hasOwnProperty.call(element, "design_variables")) {
    return false;
  }
  const variables = element.design_variables;
  return variables != null && (!Array.isArray(variables) || variables.length > 0);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isBoundedNumber(
  value: unknown,
  minimum: number,
  maximum: number
): value is number {
  return (
    isFiniteNumber(value) &&
    value >= minimum &&
    value <= maximum
  );
}

function hasSupportedOptionalOpacity(value: unknown): boolean {
  return value == null || isBoundedNumber(value, 0, 1);
}

function hasSupportedVectorPaint(
  value: unknown,
  options: { stroke?: boolean } = {}
): boolean {
  if (value == null) return true;
  if (!isRecord(value) || !isTemplateV2SafeColor(value.color)) return false;
  if (!hasSupportedOptionalOpacity(value.opacity)) return false;
  if (!options.stroke) return true;
  if (!isBoundedNumber(value.width, 0, 100)) return false;
  if (value.dash == null) return true;
  return (
    Array.isArray(value.dash) &&
    value.dash.every((part) => isBoundedNumber(part, 0, 1000))
  );
}

function hasSupportedVectorShadow(value: unknown): boolean {
  if (value == null) return true;
  if (!isRecord(value)) return false;
  if (value.color != null && !isTemplateV2SafeColor(value.color)) return false;
  if (value.blur != null && !isBoundedNumber(value.blur, 0, 1000)) return false;
  if (!hasSupportedOptionalOpacity(value.opacity)) return false;
  for (const offset of [
    value.offset_x,
    value.offset_y,
    value.offsetX,
    value.offsetY,
  ]) {
    if (offset != null && !isFiniteNumber(offset)) return false;
  }
  return true;
}

function hasSupportedVectorContract(element: TemplateV2ToolbarRecord): boolean {
  if (!Array.isArray(element.points) || element.points.length < 2) return false;
  if (
    element.shape != null &&
    element.shape !== "polygon" &&
    element.shape !== "ellipse"
  ) {
    return false;
  }
  if (element.shape === "ellipse" && element.points.length !== 2) return false;
  if (!element.points.every(
    (point) =>
      isRecord(point) && isFiniteNumber(point.x) && isFiniteNumber(point.y)
  )) {
    return false;
  }
  const cornerRadii = element.corner_radii ?? element.cornerRadii;
  if (
    cornerRadii != null &&
    (!Array.isArray(cornerRadii) ||
      cornerRadii.length > element.points.length ||
      !cornerRadii.every((radius) => isBoundedNumber(radius, 0, 10000)))
  ) {
    return false;
  }
  if (!hasSupportedOptionalOpacity(element.opacity)) return false;
  if (!hasSupportedVectorPaint(element.fill)) return false;
  if (!hasSupportedVectorPaint(element.stroke, { stroke: true })) return false;
  if (!hasSupportedVectorShadow(element.shadow)) return false;

  // The shared planner validates curve, rotation, size, and point geometry.
  // Dash is fidelity-supported by Studio/export, so only its non-mutating
  // planning projection is used here.
  return Boolean(
    planStudioElement(projectTemplateV2ElementForRenderPlan(element))
  );
}

function hasSupportedChartContract(element: TemplateV2ToolbarRecord): boolean {
  // Validation intentionally does not normalize. Rich controls must not appear
  // for stale or malformed payloads that the toolbar could otherwise mutate.
  return validateChartContract(element).ok;
}

function hasSupportedTableContract(element: TemplateV2ToolbarRecord): boolean {
  // As with charts, do not synthesize missing table bounds in the toolbar.
  // The enabled fill controls require both a header cell and a body cell.
  // Structurally valid zero-sized tables remain renderable, but their toolbar
  // stays fail-closed until there is a meaningful cell-level operation.
  return (
    validateTableContract(element).ok &&
    Array.isArray(element.columns) &&
    element.columns.length > 0 &&
    Array.isArray(element.rows) &&
    element.rows.length > 0 &&
    Array.isArray(element.rows[0]) &&
    element.rows[0].length > 0
  );
}

function hasSupportedInfographicContract(
  element: TemplateV2ToolbarRecord
): boolean {
  try {
    validateTemplateV2InfographicContract(element);
    return true;
  } catch {
    return false;
  }
}

export function resolveTemplateV2ToolbarRoute({
  selectionCount,
  element,
}: {
  selectionCount: number;
  element: unknown;
}): TemplateV2ToolbarRoute {
  const count = Number.isFinite(selectionCount)
    ? Math.max(0, Math.floor(selectionCount))
    : 0;
  if (count === 0) return { kind: "none" };
  if (count > 1) return { kind: "multi", count };

  if (!isRecord(element)) {
    return { kind: "unsupported", reason: "missing-element", element: null };
  }
  if (hasUnsafeDesignVariables(element)) {
    return { kind: "unsupported", reason: "design-variables", element };
  }

  if (element.type === "image") {
    return {
      kind: element.is_icon === true ? "icon" : "image",
      element,
    };
  }
  if (element.type === "text" || element.type === "text-list") {
    return { kind: element.type, element };
  }
  if (element.type === "vector") {
    return hasSupportedVectorContract(element)
      ? { kind: "vector", element }
      : { kind: "unsupported", reason: "invalid-contract", element };
  }
  if (element.type === "chart") {
    return hasSupportedChartContract(element)
      ? { kind: "chart", element }
      : { kind: "unsupported", reason: "invalid-contract", element };
  }
  if (element.type === "table") {
    return hasSupportedTableContract(element)
      ? { kind: "table", element }
      : { kind: "unsupported", reason: "invalid-contract", element };
  }
  if (element.type === "infographic") {
    return hasSupportedInfographicContract(element)
      ? { kind: "infographic", element }
      : { kind: "unsupported", reason: "invalid-contract", element };
  }
  return { kind: "unsupported", reason: "element-type", element };
}

const finite = (value: number, fallback = 0) =>
  Number.isFinite(value) ? value : fallback;

const clamp = (value: number, minimum: number, maximum: number) =>
  Math.min(Math.max(value, minimum), Math.max(minimum, maximum));

/**
 * Projects a Konva/client box into viewport coordinates. Scale is applied to
 * stage coordinates; pan is already expressed in viewport pixels.
 */
export function projectTemplateV2ToolbarBox(
  box: TemplateV2ToolbarBox,
  viewport: {
    left: number;
    top: number;
    scale?: number;
    scaleX?: number;
    scaleY?: number;
    panX?: number;
    panY?: number;
    scrollLeft?: number;
    scrollTop?: number;
  }
): TemplateV2ToolbarBox {
  const scaleX = finite(viewport.scaleX ?? viewport.scale ?? 1, 1);
  const scaleY = finite(viewport.scaleY ?? viewport.scale ?? 1, 1);
  return {
    x:
      finite(viewport.left) +
      finite(box.x) * scaleX +
      finite(viewport.panX ?? 0) -
      finite(viewport.scrollLeft ?? 0),
    y:
      finite(viewport.top) +
      finite(box.y) * scaleY +
      finite(viewport.panY ?? 0) -
      finite(viewport.scrollTop ?? 0),
    width: Math.max(0, finite(box.width) * Math.abs(scaleX)),
    height: Math.max(0, finite(box.height) * Math.abs(scaleY)),
  };
}

export function positionTemplateV2Toolbar({
  anchor,
  bounds,
  toolbarWidth,
  toolbarHeight,
  gap = 8,
  margin = 8,
}: {
  anchor: TemplateV2ToolbarBox | null;
  bounds: TemplateV2ToolbarBounds;
  toolbarWidth: number;
  toolbarHeight: number;
  gap?: number;
  margin?: number;
}): TemplateV2ToolbarPosition {
  const width = Math.max(0, finite(toolbarWidth));
  const height = Math.max(0, finite(toolbarHeight));
  const safeMargin = Math.max(0, finite(margin));
  const safeGap = Math.max(0, finite(gap));
  const minLeft = finite(bounds.left) + safeMargin;
  const maxLeft = finite(bounds.right) - safeMargin - width;
  const minTop = finite(bounds.top) + safeMargin;
  const maxTop = finite(bounds.bottom) - safeMargin - height;

  if (!anchor) {
    return {
      left: minLeft,
      top: minTop,
      placement: "fallback",
      visible: false,
    };
  }

  const desiredLeft = anchor.x + anchor.width / 2 - width / 2;
  const aboveTop = anchor.y - safeGap - height;
  const belowTop = anchor.y + anchor.height + safeGap;
  const canFitAbove = aboveTop >= minTop;
  const canFitBelow = belowTop <= maxTop;
  const placement = canFitAbove || !canFitBelow ? "above" : "below";
  const desiredTop = placement === "above" ? aboveTop : belowTop;

  return {
    left: clamp(desiredLeft, minLeft, maxLeft),
    top: clamp(desiredTop, minTop, maxTop),
    placement,
    visible: true,
  };
}
