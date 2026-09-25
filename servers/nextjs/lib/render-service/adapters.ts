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

const svgAdapter = adapter({
  driver_id: "svg-v1",
  renderer_adapter_id: "svg-v1",
  plan(request): RenderAdapterPlan {
    for (const slide of request.snapshot.slides) {
      if (slide.source_format !== "svg" || typeof slide.svg_content !== "string" || !slide.svg_content.trim()) {
        throw new Error("render_svg_source_required");
      }
    }
    return {
      renderer_version: "svg-browser@1",
      runtime_dependencies: [BASE_LAYOUT_RUNTIME],
      payload: { slides: clone(request.snapshot.slides) },
    };
  },
});

export const PRESENTATION_RENDER_ADAPTER_REGISTRY = Object.freeze({
  authored: authoredAdapter,
  "svg-v1": svgAdapter,
} satisfies PresentationRenderAdapterRegistry);
