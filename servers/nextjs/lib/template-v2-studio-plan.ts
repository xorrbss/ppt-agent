import {
  createTemplateV2SlideRenderPlan,
  type TemplateV2PlanFrame,
  type TemplateV2RenderPlanNode,
} from "./template-v2-render-plan.mjs";
import { isJsonRecord, type JsonRecord } from "./template-v2-studio.ts";

export type { TemplateV2PlanFrame, TemplateV2RenderPlanNode };

// Per-element bridge from the export render plan to the Studio canvas. Planning a
// single synthesized component isolates failures: one invalid element (e.g. a
// chart resized under the 80x60 export minimum) degrades to a placeholder instead
// of taking down the whole scene, which the slide-level planner would do.
export function planStudioElement(
  element: JsonRecord
): TemplateV2RenderPlanNode | null {
  try {
    const plan = createTemplateV2SlideRenderPlan({
      ui: {
        components: [
          { id: "studio", position: { x: 0, y: 0 }, elements: [element] },
        ],
      },
    });
    return plan.components[0]?.elements[0] ?? null;
  } catch {
    return null;
  }
}

// Concrete pixel box for a plan frame; the planner leaves width/height null when
// the element inherits or auto-sizes, which the canvas resolves to a fallback.
export function resolveStudioPlanFrame(
  frame: TemplateV2PlanFrame,
  fallback = { width: 240, height: 80 }
) {
  return {
    x: frame.x,
    y: frame.y,
    width: Math.max(1, frame.width ?? fallback.width),
    height: Math.max(1, frame.height ?? fallback.height),
  };
}

// Rebase a flex/grid child for standalone rendering inside a wrapper Group that
// already sits at the planned placement: position collapses to the origin and the
// planned placement becomes the explicit size. Vector children are excluded by
// the caller — their geometry comes from points, not position/size.
export function rebaseStudioChild(
  child: JsonRecord,
  frame: TemplateV2PlanFrame
): JsonRecord {
  const rebased: JsonRecord = {
    ...child,
    position: { x: 0, y: 0 },
  };
  if (frame.width !== null && frame.height !== null) {
    const size = isJsonRecord(child.size) ? child.size : {};
    rebased.size = { ...size, width: frame.width, height: frame.height };
  }
  return rebased;
}
