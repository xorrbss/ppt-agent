import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applyTemplateV2DirectDesignVariableOption,
  inspectTemplateV2DesignVariableEditCapability,
  inspectTemplateV2DesignVariablesContract,
} from "./template-v2-design-variables-contract.ts";
import { renderTemplateV2GeneralPresentationHtml } from "./template-v2-general-renderer.mjs";
import { createTemplateV2SlideRenderPlan } from "./template-v2-render-plan.mjs";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";

type FixtureDesignVariable = {
  name: string;
  type: string;
  options: string[];
  effect: Array<{
    path: string;
    effect: string;
    [key: string]: unknown;
  }>;
  [key: string]: unknown;
};

type FixtureElement = {
  runs: Array<{
    font: {
      color: string;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  }>;
  design_variables: FixtureDesignVariable[];
  [key: string]: unknown;
};

type FixtureLayout = {
  components: Array<{
    elements: FixtureElement[];
    [key: string]: unknown;
  }>;
  [key: string]: unknown;
};

type FixtureWire = {
  layouts: {
    layouts: FixtureLayout[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

type DesignVariablesFixture = {
  contract: {
    upstream_sha: string;
    reviewed_slice: Record<string, unknown>;
  };
  edit: {
    variable_index: number;
    option_index: number;
  };
  wire: FixtureWire;
};

const fixture = JSON.parse(
  readFileSync(
    new URL(
      "./fixtures/template-v2-upstream-design-variables-57b194b.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as DesignVariablesFixture;

test("pinned upstream direct-color Design Variable survives import, edit, serialize, JSON reload, and render", () => {
  assert.equal(
    fixture.contract.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83",
  );
  assert.deepEqual(fixture.contract.reviewed_slice, {
    element_type: "text",
    run_count: 1,
    variable_count: 1,
    variable_type: "color",
    option_count: 2,
    effect_count: 1,
    effect_path: "runs.0.font.color",
    effect_expression: "$",
  });

  const originalWire = structuredClone(fixture.wire);
  const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
  const studioLayouts = structuredClone(document.studioLayouts) as {
    layouts: Array<{
      components: Array<{ elements: Array<Record<string, unknown>> }>;
    }>;
  };
  const originalElement = studioLayouts.layouts[0]!.components[0]!
    .elements[0]!;
  assert.deepEqual(
    inspectTemplateV2DesignVariableEditCapability(originalElement),
    {
      activation: "direct-option",
      variableIndex: 0,
      name: "title_color",
      options: ["#0F172A", "#1D4ED8"],
      selectedOptionIndex: 0,
      targetPath: "runs.0.font.color",
    },
  );

  const editedElement = applyTemplateV2DirectDesignVariableOption(
    originalElement,
    {
      variableIndex: fixture.edit.variable_index,
      optionIndex: fixture.edit.option_index,
    },
  );
  studioLayouts.layouts[0]!.components[0]!.elements[0] = editedElement;
  assert.notEqual(editedElement, originalElement);
  assert.equal(
    (
      (originalElement.runs as Array<{ font: { color: string } }>)[0]!
        .font
    ).color,
    "#0F172A",
  );

  const saved = serializeStudioLayoutsForUpstream(
    document,
    studioLayouts,
  ) as FixtureWire;
  const savedElement = saved.layouts.layouts[0]!.components[0]!.elements[0]!;
  const expectedWire = structuredClone(fixture.wire);
  expectedWire.layouts.layouts[0].components[0].elements[0].runs[0].font.color =
    "#1D4ED8";
  assert.deepEqual(saved, expectedWire);
  assert.deepEqual(
    savedElement.design_variables,
    fixture.wire.layouts.layouts[0].components[0].elements[0].design_variables,
  );

  const persistedWire = JSON.parse(JSON.stringify(saved));
  const reloadedDocument =
    adaptUpstreamTemplateV2LayoutsToStudio(persistedWire);
  const reloadedWire = serializeStudioLayoutsForUpstream(
    reloadedDocument,
  ) as FixtureWire;
  assert.deepEqual(reloadedWire, expectedWire);
  const reloadedLayout = (
    reloadedDocument.studioLayouts.layouts as FixtureLayout[]
  )[0]!;
  const reloadedElement = reloadedLayout.components[0]!.elements[0]!;
  assert.deepEqual(
    inspectTemplateV2DesignVariableEditCapability(reloadedElement),
    {
      activation: "direct-option",
      variableIndex: 0,
      name: "title_color",
      options: ["#0F172A", "#1D4ED8"],
      selectedOptionIndex: 1,
      targetPath: "runs.0.font.color",
    },
  );

  // Render strictly from the JSON-reloaded and re-adapted layout, never from
  // the pre-persistence in-memory object.
  const slide = { id: "direct-color-design-variable", ui: reloadedLayout };
  const contract = inspectTemplateV2DesignVariablesContract(slide);
  assert.equal(contract.activation, "retention-only");
  assert.equal(contract.toolbarSafe, false);
  assert.deepEqual(
    contract.declarations[0]!.bindings,
    fixture.wire.layouts.layouts[0].components[0].elements[0].design_variables,
  );
  assert.doesNotThrow(() => createTemplateV2SlideRenderPlan(slide));
  const html = renderTemplateV2GeneralPresentationHtml({
    version: "v2-standard",
    mode: "template",
    slides: [slide],
  });
  assert.match(html, /Bound title/);
  assert.match(html, /color:#1D4ED8/);
  assert.deepEqual(fixture.wire, originalWire);
});

test("unproven Design Variable selectors and expressions remain retention-only", () => {
  const originalElement =
    fixture.wire.layouts.layouts[0].components[0].elements[0];
  const unprovenElements = [
    {
      ...structuredClone(originalElement),
      design_variables: [
        {
          ...structuredClone(originalElement.design_variables[0]),
          effect: [{ path: "runs.0.font.color", effect: "$.token" }],
        },
      ],
    },
    {
      ...structuredClone(originalElement),
      design_variables: [
        {
          ...structuredClone(originalElement.design_variables[0]),
          effect: [{ path: "runs.0.font.color", effect: "round($)" }],
        },
      ],
    },
    {
      ...structuredClone(originalElement),
      design_variables: [
        {
          ...structuredClone(originalElement.design_variables[0]),
          effect: [{ path: "runs.0.font.color", effect: "$ * 2" }],
        },
      ],
    },
    {
      ...structuredClone(originalElement),
      design_variables: [
        {
          ...structuredClone(originalElement.design_variables[0]),
          effect: [{ path: "text.runs.0.font.color", effect: "$" }],
        },
      ],
    },
    {
      ...structuredClone(originalElement),
      design_variables: [
        {
          ...structuredClone(originalElement.design_variables[0]),
          effect: [
            { path: "runs.0.font.color", effect: "$" },
            { path: "font.color", effect: "$" },
          ],
        },
      ],
    },
    {
      ...structuredClone(originalElement),
      design_variables: [
        ...structuredClone(originalElement.design_variables),
        structuredClone(originalElement.design_variables[0]),
      ],
    },
    {
      ...structuredClone(originalElement),
      design_variables: [
        {
          ...structuredClone(originalElement.design_variables[0]),
          options: ["#0F172A", "red;background:url(unsafe)"],
        },
      ],
    },
    {
      ...structuredClone(originalElement),
      design_variables: [
        {
          ...structuredClone(originalElement.design_variables[0]),
          future_binding: true,
        },
      ],
    },
  ];

  unprovenElements.forEach((element) => {
    assert.deepEqual(inspectTemplateV2DesignVariableEditCapability(element), {
      activation: "retention-only",
    });
    assert.throws(
      () =>
        applyTemplateV2DirectDesignVariableOption(element, {
          variableIndex: 0,
          optionIndex: 1,
        }),
      /template_v2_design_variables_unproven_edit/,
    );
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
