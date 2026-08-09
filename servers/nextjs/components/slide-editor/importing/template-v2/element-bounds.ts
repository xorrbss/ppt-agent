/**
 * Stage 3 (map to schema) — bounds of already-adapted elements. Shared by the
 * group frame derivation and by the slide-background detection.
 */
import {
  EDITOR_STAGE_HEIGHT,
  EDITOR_STAGE_WIDTH,
  type SlideElement,
} from "@/components/slide-editor/types";
import {
  MIN_ELEMENT_SIZE,
  clamp,
  round,
  type AdaptedPosition,
  type AdaptedSize,
} from "@/components/slide-editor/importing/template-v2/values";
import type {
  AdaptedBaseElement,
  AdaptedRequiredBaseElement,
} from "@/components/slide-editor/importing/template-v2/element-base";

export function groupFrame(
  base: AdaptedBaseElement,
  children: SlideElement[],
): AdaptedRequiredBaseElement {
  const bounds = childrenBounds(children);
  return {
    ...base,
    position: base.position ?? { x: 0, y: 0 },
    size: base.size ?? bounds,
  };
}

function childrenBounds(children: SlideElement[]): AdaptedSize {
  if (children.length === 0) {
    return { width: MIN_ELEMENT_SIZE, height: MIN_ELEMENT_SIZE };
  }

  const bounds = children.reduce(
    (acc, child) => {
      const childBounds = adaptedElementBounds(child);
      return {
        width: Math.max(acc.width, childBounds.x + childBounds.width),
        height: Math.max(acc.height, childBounds.y + childBounds.height),
      };
    },
    { width: MIN_ELEMENT_SIZE, height: MIN_ELEMENT_SIZE },
  );

  return {
    width: clamp(round(bounds.width), MIN_ELEMENT_SIZE, EDITOR_STAGE_WIDTH),
    height: clamp(round(bounds.height), MIN_ELEMENT_SIZE, EDITOR_STAGE_HEIGHT),
  };
}

export function adaptedElementBounds(child: SlideElement): {
  x: number;
  y: number;
  width: number;
  height: number;
} {
  if (
    child.type === "vector" &&
    Array.isArray(child.points)
  ) {
    const points = child.points.filter(
      (point): point is AdaptedPosition =>
        typeof point.x === "number" && typeof point.y === "number",
    );
    if (points.length > 0) {
      const minX = Math.min(...points.map((point) => point.x));
      const minY = Math.min(...points.map((point) => point.y));
      const maxX = Math.max(...points.map((point) => point.x));
      const maxY = Math.max(...points.map((point) => point.y));
      const strokeWidth = Math.max(1, child.stroke?.width ?? 1);
      return {
        x: minX,
        y: minY,
        width: Math.max(maxX - minX, strokeWidth, MIN_ELEMENT_SIZE),
        height: Math.max(maxY - minY, strokeWidth, MIN_ELEMENT_SIZE),
      };
    }
  }

  return {
    x: child.position?.x ?? 0,
    y: child.position?.y ?? 0,
    width: child.size?.width ?? MIN_ELEMENT_SIZE,
    height: child.size?.height ?? MIN_ELEMENT_SIZE,
  };
}
