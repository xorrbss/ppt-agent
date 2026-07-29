import assert from "node:assert/strict";
import test from "node:test";

import { inspectTemplateV2DesignVariablesContract } from "./template-v2-design-variables-contract.ts";
import { renderTemplateV2GeneralPresentationHtml } from "./template-v2-general-renderer.mjs";
import { createTemplateV2SlideRenderPlan } from "./template-v2-render-plan.mjs";
import { updateTemplateV2ContentRun } from "./template-v2-studio-content.ts";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";

test("design-variable text edits preserve bindings through save, reload, and export", () => {
  const designVariables = [
    {
      name: "title_color",
      target: { path: "font.color", mode: "theme" },
      fallback: "#1d4ed8",
    },
  ];
  const source = {
    future_top_level_field: { source: "generated" },
    layouts: {
      future_envelope_field: ["retain", "exactly"],
      layouts: [
        {
          id: "bound-title",
          future_layout_field: true,
          components: [
            {
              id: "hero",
              position: { x: 0, y: 0 },
              elements: [
                {
                  type: "text",
                  name: "title",
                  position: { x: 20, y: 30 },
                  size: { width: 600, height: 80 },
                  runs: [{ text: "Original title" }],
                  design_variables: designVariables,
                  future_element_field: { retain: "exactly" },
                },
              ],
            },
          ],
        },
      ],
    },
  };

  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const studioLayouts = structuredClone(document.studioLayouts) as {
    layouts: Array<{
      components: Array<{ elements: Array<Record<string, unknown>> }>;
    }>;
  };
  const originalElement = studioLayouts.layouts[0]!.components[0]!
    .elements[0]!;
  studioLayouts.layouts[0]!.components[0]!.elements[0] =
    updateTemplateV2ContentRun(
      originalElement,
      { kind: "text", runIndex: 0 },
      "Edited bound title",
    );

  const saved = serializeStudioLayoutsForUpstream(document, studioLayouts) as {
    layouts: { layouts: Array<{ components: Array<{ elements: unknown[] }> }> };
  };
  const savedElement = saved.layouts.layouts[0]!.components[0]!.elements[0]!;

  assert.deepEqual(saved, {
    ...source,
    layouts: {
      ...source.layouts,
      layouts: [
        {
          ...source.layouts.layouts[0],
          components: [
            {
              ...source.layouts.layouts[0]!.components[0],
              elements: [
                {
                  ...source.layouts.layouts[0]!.components[0]!.elements[0],
                  runs: [{ text: "Edited bound title" }],
                },
              ],
            },
          ],
        },
      ],
    },
  });

  // The export plan receives a strict slide.ui boundary and neither planning
  // nor rendering may mutate the persisted design-variable metadata.
  const slide = { id: "bound-title", ui: saved.layouts.layouts[0] };
  const contract = inspectTemplateV2DesignVariablesContract(slide);
  assert.equal(contract.activation, "retention-only");
  assert.equal(contract.toolbarSafe, false);
  assert.deepEqual(contract.declarations[0]!.bindings, designVariables);
  assert.doesNotThrow(() => createTemplateV2SlideRenderPlan(slide));
  const html = renderTemplateV2GeneralPresentationHtml({
    version: "v2-standard",
    mode: "template",
    slides: [slide],
  });
  assert.match(html, /Edited bound title/);
  assert.deepEqual(savedElement, {
    ...source.layouts.layouts[0]!.components[0]!.elements[0],
    runs: [{ text: "Edited bound title" }],
  });
});

test("design-variable payloads fail closed when no strict slide.ui boundary exists", () => {
  assert.throws(
    () =>
      createTemplateV2SlideRenderPlan({
        components: [
          {
            elements: [
              {
                type: "text",
                runs: [{ text: "unsafe" }],
                design_variables: [{ name: "title_color" }],
              },
            ],
          },
        ],
      }),
    /template_v2_render_plan_ui_required/,
  );
});
