import {
  materializeTemplateRenderPlan,
} from "../template-v2-ast/index.ts";
import { createTemplateV2SlideRenderPlan } from "../template-v2-render-plan.mjs";
import type {
  PresentationRenderAdapter,
  PresentationRenderAdapterRegistry,
  RenderAdapterPlan,
  RenderRuntimeDependency,
} from "./contracts.ts";

function clone<T>(value: T): T {
  return structuredClone(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function persistedTemplateV2Slide(
  slide: Readonly<Record<string, unknown>>,
  materializedUi?: Record<string, unknown>,
): Record<string, unknown> {
  const ui = materializedUi ?? (isRecord(slide.ui) ? clone(slide.ui) : null);
  if (!ui || !Array.isArray(ui.components)) {
    throw new Error("render_template_v2_hydrated_ui_required");
  }
  const hydrated = { ...clone(slide), ui };
  // This is the canonical frontend compiler used by editor preview and export.
  // It fails closed before a persisted hydration facade reaches a renderer.
  createTemplateV2SlideRenderPlan(hydrated);
  return hydrated;
}

const BASE_LAYOUT_RUNTIME = Object.freeze({
  id: "presenton-layout-runtime",
  version: "1",
  purpose: "layout",
} satisfies RenderRuntimeDependency);

function adapter<const Adapter extends PresentationRenderAdapter>(
  definition: Adapter,
): Readonly<Adapter> {
  return Object.freeze(definition);
}

const templateV2Adapter = adapter({
  driver_id: "template-v2",
  renderer_adapter_id: "template-v2",
  plan(request): RenderAdapterPlan {
    const plans = request.template_render_plans;
    if (plans && plans.length !== request.snapshot.slides.length) {
      throw new Error("render_template_v2_plan_count_mismatch");
    }
    for (const plan of plans ?? []) {
      if (
        plan.schema_version !== "presenton.template-render-plan/v1" ||
        plan.capabilities.review_required === true ||
        plan.capabilities.html_render !== true ||
        plan.capabilities.pptx_compile !== true
      ) {
        throw new Error("render_template_v2_plan_unsupported");
      }
    }
    const slides = request.snapshot.slides.map((slide, index) =>
      persistedTemplateV2Slide(
        slide,
        plans ? materializeTemplateRenderPlan(plans[index]) : undefined,
      )
    );
    return {
      renderer_version: "template-v2-canonical@1",
      runtime_dependencies: [BASE_LAYOUT_RUNTIME],
      payload: {
        ...(plans ? { render_plans: clone(plans) } : {}),
        slides,
        materialization: plans
          ? "canonical-render-plan"
          : "persisted-hydrated-ui-facade",
      },
    };
  },
});

const authoredAdapter = adapter({
  driver_id: "authored",
  renderer_adapter_id: "authored",
  plan(request): RenderAdapterPlan {
    return {
      renderer_version: "authored-browser@1",
      runtime_dependencies: [
        BASE_LAYOUT_RUNTIME,
        {
          id: "chromium-html-materializer",
          version: "1",
          purpose: "export",
        },
      ],
      payload: { slides: clone(request.snapshot.slides) },
    };
  },
});

export const PRESENTATION_RENDER_ADAPTER_REGISTRY = Object.freeze({
  "template-v2": templateV2Adapter,
  authored: authoredAdapter,
} satisfies PresentationRenderAdapterRegistry);
