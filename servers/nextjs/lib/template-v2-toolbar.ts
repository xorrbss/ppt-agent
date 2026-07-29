export type TemplateV2ToolbarRecord = Record<string, unknown>;

export type TemplateV2ToolbarRoute =
  | { kind: "none" }
  | { kind: "multi"; count: number }
  | {
      kind: "image" | "icon" | "text" | "text-list";
      element: TemplateV2ToolbarRecord;
    }
  | {
      kind: "unsupported";
      reason: "missing-element" | "design-variables" | "element-type";
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
