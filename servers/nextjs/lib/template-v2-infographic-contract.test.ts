import assert from "node:assert/strict";
import test from "node:test";

import { renderTemplateV2GeneralPresentationHtml } from "./template-v2-general-renderer.mjs";
import {
  buildTemplateV2InfographicValuePatches,
  validateTemplateV2InfographicContract,
  validateTemplateV2SlideUiInfographicContract,
} from "./template-v2-infographic-contract.ts";
import { templateV2InfographicView } from "./template-v2-konva.ts";
import { createTemplateV2SlideRenderPlan } from "./template-v2-render-plan.mjs";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";

function infographicElement(overrides: Record<string, unknown> = {}) {
  return {
    type: "infographic",
    position: { x: 10, y: 20 },
    size: { width: 300, height: 40 },
    data: {
      type: "progress_bar",
      min_value: -20,
      max_value: 80,
      value: 30,
      future_format: "percent",
    },
    colors: ["#E5E7EB", "#2563EB"],
    upstream_element_extension: { retain: "exactly" },
    ...overrides,
  };
}

function slideWith(element: Record<string, unknown>) {
  return {
    id: "infographic",
    ui: {
      components: [
        {
          id: "metric",
          position: { x: 0, y: 0 },
          elements: [element],
        },
      ],
    },
  };
}

test("infographic contract retains unknown JSON and derives an unclamped render ratio", () => {
  const source = infographicElement();
  const contract = validateTemplateV2InfographicContract(source);

  assert.deepEqual(contract.json, source);
  assert.deepEqual(contract.metrics, {
    type: "progress_bar",
    minimum: -20,
    maximum: 80,
    value: 30,
    ratio: 0.5,
    colors: ["#E5E7EB", "#2563EB"],
  });

  (contract.json.data as Record<string, unknown>).future_format = "changed";
  assert.equal(
    (source.data as Record<string, unknown>).future_format,
    "percent",
  );
});

test("infographic contract accepts inclusive endpoints for progress bars and gauges", () => {
  for (const type of ["progress_bar", "gauge"] as const) {
    for (const value of [10, 20]) {
      const contract = validateTemplateV2InfographicContract(
        infographicElement({
          data: { type, min_value: 10, max_value: 20, value },
        }),
      );
      assert.equal(contract.metrics.type, type);
      assert.equal(contract.metrics.ratio, value === 10 ? 0 : 1);
    }
  }
});

test("value edits are bounded leaf patches that do not mutate retained JSON", () => {
  const source = infographicElement();
  const before = structuredClone(source);

  assert.deepEqual(buildTemplateV2InfographicValuePatches(source, 64), {
    ok: true,
    value: [{ path: ["data", "value"], value: 64 }],
  });
  assert.deepEqual(buildTemplateV2InfographicValuePatches(source, 30), {
    ok: true,
    value: [],
  });
  assert.deepEqual(
    buildTemplateV2InfographicValuePatches(source, Number.NaN),
    {
      ok: false,
      reason: "template_v2_infographic_invalid_value_edit",
    },
  );
  assert.deepEqual(buildTemplateV2InfographicValuePatches(source, 81), {
    ok: false,
    reason: "template_v2_infographic_value_edit_out_of_range",
  });
  assert.equal(
    buildTemplateV2InfographicValuePatches(
      infographicElement({
        data: {
          type: "progress_bar",
          min_value: 5,
          max_value: 5,
          value: 5,
        },
      }),
      5,
    ).ok,
    false,
  );
  assert.deepEqual(source, before);
});

test("value leaf patch survives save, reload, Konva planning, and the PPT render boundary", () => {
  const sourceElement = infographicElement();
  const source = {
    future_envelope: { retain: "exactly" },
    layouts: [
      {
        id: "retained-infographic",
        future_layout: ["retain", "exactly"],
        components: [
          {
            id: "metric",
            position: { x: 0, y: 0 },
            elements: [sourceElement],
          },
        ],
      },
    ],
  };
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(document.studioLayouts) as {
    layouts: Array<{
      components: Array<{
        elements: Array<Record<string, unknown>>;
      }>;
    }>;
  };
  const editedElement = edited.layouts[0]!.components[0]!.elements[0]!;
  const patches = buildTemplateV2InfographicValuePatches(editedElement, 72.5);
  assert.equal(patches.ok, true);
  if (!patches.ok) return;
  assert.deepEqual(patches.value, [
    { path: ["data", "value"], value: 72.5 },
  ]);
  (editedElement.data as Record<string, unknown>).value =
    patches.value[0]!.value;

  const saved = serializeStudioLayoutsForUpstream(document, edited) as {
    future_envelope: { retain: string };
    layouts: Array<{
      future_layout: string[];
      components: Array<{ elements: Array<Record<string, unknown>> }>;
    }>;
  };
  const reloaded = adaptUpstreamTemplateV2LayoutsToStudio(saved);
  const persisted = serializeStudioLayoutsForUpstream(reloaded) as typeof saved;
  const persistedElement =
    persisted.layouts[0]!.components[0]!.elements[0]!;
  const slide = { id: "retained-infographic", ui: persisted.layouts[0] };

  assert.deepEqual(persisted.future_envelope, source.future_envelope);
  assert.deepEqual(persisted.layouts[0]!.future_layout, ["retain", "exactly"]);
  assert.deepEqual(persistedElement.upstream_element_extension, {
    retain: "exactly",
  });
  assert.deepEqual(
    (persistedElement.data as Record<string, unknown>).future_format,
    "percent",
  );
  assert.equal(
    validateTemplateV2SlideUiInfographicContract(slide, 0, 0).metrics.value,
    72.5,
  );
  assert.equal(templateV2InfographicView(persistedElement)?.ratio, 0.925);
  assert.equal(
    createTemplateV2SlideRenderPlan(slide).components[0]!.elements[0]!
      .infographic?.ratio,
    0.925,
  );

  const html = renderTemplateV2GeneralPresentationHtml({
    version: "v2-standard",
    mode: "template",
    slides: [slide],
  });
  assert.match(html, /data-infographic-type="progress_bar"/);
  assert.match(html, /width:92.5%/);
  assert.deepEqual(sourceElement, infographicElement());
});

test("infographic contract rejects invalid ranges, values, colors, and types", () => {
  for (const data of [
    { type: "progress_bar", min_value: 10, max_value: 10, value: 10 },
    { type: "progress_bar", min_value: 20, max_value: 10, value: 15 },
    {
      type: "progress_bar",
      min_value: Number.NEGATIVE_INFINITY,
      max_value: 10,
      value: 0,
    },
  ]) {
    assert.throws(
      () => validateTemplateV2InfographicContract(infographicElement({ data })),
      /template_v2_infographic_(?:non_json|invalid_range)/,
    );
  }

  for (const value of [-1, 11, Number.NaN]) {
    assert.throws(
      () =>
        validateTemplateV2InfographicContract(
          infographicElement({
            data: {
              type: "gauge",
              min_value: 0,
              max_value: 10,
              value,
            },
          }),
        ),
      /template_v2_infographic_(?:non_json|invalid_value)/,
    );
  }

  assert.throws(
    () =>
      validateTemplateV2InfographicContract(
        infographicElement({
          data: {
            type: "dial",
            min_value: 0,
            max_value: 10,
            value: 5,
          },
        }),
      ),
    /template_v2_infographic_unsupported_type/,
  );
  assert.throws(
    () =>
      validateTemplateV2InfographicContract(
        infographicElement({ colors: ["#fff", 7] }),
      ),
    /template_v2_infographic_invalid_colors/,
  );
});

test("infographic slide contract requires the persisted slide.ui boundary", () => {
  const element = infographicElement();
  assert.throws(
    () =>
      validateTemplateV2SlideUiInfographicContract(
        { components: [{ elements: [element] }] },
        0,
        0,
      ),
    /template_v2_infographic_slide_ui_required/,
  );

  assert.deepEqual(
    validateTemplateV2SlideUiInfographicContract(slideWith(element), 0, 0)
      .json,
    element,
  );
});

test("strict render and export accept valid infographics and reject invalid values", () => {
  const validSlide = slideWith(
    infographicElement({
      data: {
        type: "progress_bar",
        min_value: 0,
        max_value: 100,
        value: 64,
      },
    }),
  );
  const plan = createTemplateV2SlideRenderPlan(validSlide);
  assert.equal(plan.components[0]!.elements[0]!.infographic?.ratio, 0.64);

  const html = renderTemplateV2GeneralPresentationHtml({
    version: "v2-standard",
    mode: "template",
    slides: [validSlide],
  });
  assert.match(html, /data-infographic-type="progress_bar"/);
  assert.match(html, /width:64%/);

  const invalidSlide = slideWith(
    infographicElement({
      data: {
        type: "progress_bar",
        min_value: 100,
        max_value: 0,
        value: 50,
      },
    }),
  );
  assert.throws(
    () => createTemplateV2SlideRenderPlan(invalidSlide),
    /template_v2_render_plan_invalid_infographic_range/,
  );
  assert.throws(
    () =>
      renderTemplateV2GeneralPresentationHtml({
        version: "v2-standard",
        mode: "template",
        slides: [invalidSlide],
      }),
    /template_v2_renderer_invalid_infographic_range/,
  );
});
