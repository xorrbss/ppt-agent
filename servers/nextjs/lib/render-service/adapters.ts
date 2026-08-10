import {
  materializeTemplateRenderPlan,
} from "../template-v2-ast/index.ts";
import type {
  PresentationRenderAdapter,
  PresentationRenderAdapterRegistry,
  RenderAdapterPlan,
  RenderRuntimeDependency,
} from "./contracts.ts";

function clone<T>(value: T): T {
  return structuredClone(value);
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

const legacyAdapter = adapter({
  driver_id: "standard",
  renderer_adapter_id: "legacy-elements",
  plan(request): RenderAdapterPlan {
    return {
      renderer_version: "legacy-elements@1",
      runtime_dependencies: [BASE_LAYOUT_RUNTIME],
      payload: { slides: clone(request.snapshot.slides) },
    };
  },
});

const smartAdapter = adapter({
  driver_id: "smart",
  renderer_adapter_id: "smart-html",
  plan(request): RenderAdapterPlan {
    return {
      renderer_version: "smart-html@1",
      runtime_dependencies: [
        BASE_LAYOUT_RUNTIME,
        {
          id: "presenton-chart-snapshot-runtime",
          version: "1",
          purpose: "chart",
        },
      ],
      payload: { slides: clone(request.snapshot.slides) },
    };
  },
});

const templateV2Adapter = adapter({
  driver_id: "template-v2",
  renderer_adapter_id: "template-v2",
  plan(request): RenderAdapterPlan {
    const plans = request.template_render_plans;
    if (!plans || plans.length !== request.snapshot.slides.length) {
      throw new Error("render_template_v2_plan_required");
    }
    for (const plan of plans) {
      if (
        plan.schema_version !== "presenton.template-render-plan/v1" ||
        plan.capabilities.review_required === true ||
        plan.capabilities.html_render !== true ||
        plan.capabilities.pptx_compile !== true
      ) {
        throw new Error("render_template_v2_plan_unsupported");
      }
    }
    return {
      renderer_version: "template-v2-canonical@1",
      runtime_dependencies: [BASE_LAYOUT_RUNTIME],
      payload: {
        render_plans: clone(plans),
        slides: plans.map((plan) => materializeTemplateRenderPlan(plan)),
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
  standard: legacyAdapter,
  smart: smartAdapter,
  "template-v2": templateV2Adapter,
  authored: authoredAdapter,
} satisfies PresentationRenderAdapterRegistry);
