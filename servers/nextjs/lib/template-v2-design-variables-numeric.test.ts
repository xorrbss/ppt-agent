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

type NumericDesignVariable = {
  name: string;
  options: number[];
  effect: Array<{
    path: string;
    effect: string;
  }>;
};

type NumericElement = {
  runs: Array<{
    font: {
      size: number;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  }>;
  design_variables: NumericDesignVariable[];
  [key: string]: unknown;
};

type NumericLayout = {
  components: Array<{
    elements: NumericElement[];
    [key: string]: unknown;
  }>;
  [key: string]: unknown;
};

type NumericWire = {
  layouts: {
    layouts: NumericLayout[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

type NumericFixture = {
  contract: {
    upstream_sha: string;
    witness_kind: string;
    import_boundary: string;
    upstream_sources: string[];
    reviewed_slice: Record<string, unknown>;
  };
  edit: {
    variable_index: number;
    option_index: number;
  };
  wire: NumericWire;
};

const fixture = JSON.parse(
  readFileSync(
    new URL(
      "./fixtures/template-v2-upstream-design-variables-numeric-57b194b.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as NumericFixture;

const expectedInitialCapability = {
  activation: "direct-option",
  variableIndex: 0,
  name: "title_size",
  options: [24, 36],
  selectedOptionIndex: 0,
  targetPath: "runs.0.font.size",
};

test("pinned-contract-derived numeric Design Variable survives the Studio wire round trip", () => {
  assert.equal(
    fixture.contract.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83",
  );
  assert.equal(fixture.contract.witness_kind, "pinned-contract-derived");
  assert.equal(
    fixture.contract.import_boundary,
    "studio-upstream-wire-compatibility",
  );
  assert.deepEqual(fixture.contract.upstream_sources, [
    "servers/nextjs/components/slide-editor/types.ts",
    "servers/nextjs/components/slide-editor/importing/template-v2-import.ts",
    "servers/nextjs/components/slide-editor/model/design-variables.ts",
  ]);
  assert.deepEqual(fixture.contract.reviewed_slice, {
    element_type: "text",
    run_count: 1,
    variable_count: 1,
    variable_type: "omitted",
    options: [24, 36],
    option_policy: "exact fixed witness [24, 36]",
    effect_count: 1,
    effect_path: "runs.0.font.size",
    effect_expression: "$",
    upstream_target_scaling: "round(value * 10000) / 10000",
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
    expectedInitialCapability,
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
      (originalElement.runs as Array<{ font: { size: number } }>)[0]!
        .font
    ).size,
    24,
  );
  assert.deepEqual(
    inspectTemplateV2DesignVariableEditCapability(editedElement),
    { activation: "retention-only" },
  );
  assert.throws(
    () =>
      applyTemplateV2DirectDesignVariableOption(editedElement, {
        variableIndex: 0,
        optionIndex: 0,
      }),
    /template_v2_design_variables_unproven_edit/,
  );

  const saved = serializeStudioLayoutsForUpstream(
    document,
    studioLayouts,
  ) as NumericWire;
  const expectedWire = structuredClone(fixture.wire);
  expectedWire.layouts.layouts[0]!.components[0]!.elements[0]!.runs[0]!
    .font.size = 36;
  assert.deepEqual(saved, expectedWire);
  assert.deepEqual(
    saved.layouts.layouts[0]!.components[0]!.elements[0]!.design_variables,
    fixture.wire.layouts.layouts[0]!.components[0]!.elements[0]!
      .design_variables,
  );

  const persistedWire = JSON.parse(JSON.stringify(saved));
  const reloadedDocument =
    adaptUpstreamTemplateV2LayoutsToStudio(persistedWire);
  const reloadedWire = serializeStudioLayoutsForUpstream(
    reloadedDocument,
  ) as NumericWire;
  assert.deepEqual(reloadedWire, expectedWire);

  const reloadedLayout = (
    reloadedDocument.studioLayouts.layouts as NumericLayout[]
  )[0]!;
  const reloadedElement = reloadedLayout.components[0]!.elements[0]!;
  assert.deepEqual(
    inspectTemplateV2DesignVariableEditCapability(reloadedElement),
    { activation: "retention-only" },
  );

  const slide = { id: "direct-numeric-design-variable", ui: reloadedLayout };
  const contract = inspectTemplateV2DesignVariablesContract(slide);
  assert.equal(contract.activation, "retention-only");
  assert.equal(contract.toolbarSafe, false);
  assert.deepEqual(
    contract.declarations[0]!.bindings,
    fixture.wire.layouts.layouts[0]!.components[0]!.elements[0]!
      .design_variables,
  );
  assert.doesNotThrow(() => createTemplateV2SlideRenderPlan(slide));

  const html = renderTemplateV2GeneralPresentationHtml({
    version: "v2-standard",
    mode: "template",
    slides: [slide],
  });
  assert.match(html, /Numeric bound title/);
  assert.match(html, /font-size:36px/);
  assert.deepEqual(fixture.wire, originalWire);
});

test("unproven numeric Design Variable semantics remain retention-only", () => {
  const originalElement =
    fixture.wire.layouts.layouts[0]!.components[0]!.elements[0]!;
  const originalVariable = originalElement.design_variables[0]!;
  const withVariable = (
    variable: Record<string, unknown>,
    elementOverrides: Record<string, unknown> = {},
  ) => ({
    ...structuredClone(originalElement),
    ...elementOverrides,
    design_variables: [variable],
  });
  const withOptions = (options: unknown[]) =>
    withVariable({
      ...structuredClone(originalVariable),
      options,
    });
  const withEffect = (path: string, effect: string) =>
    withVariable({
      ...structuredClone(originalVariable),
      effect: [{ path, effect }],
    });

  const unprovenElements = [
    withVariable({
      ...structuredClone(originalVariable),
      type: "number",
    }),
    withOptions([24.5, 36]),
    withOptions([1, 512]),
    withOptions([36, 24]),
    withOptions([0, 36]),
    withOptions([24, 513]),
    withOptions([24, 24]),
    withOptions([Number.NaN, 36]),
    withEffect("runs.0.font.size", "round($)"),
    withEffect("runs.0.font.size", "$ * 2"),
    withEffect("text.runs.0.font.size", "$"),
    withVariable({
      ...structuredClone(originalVariable),
      name: "not_the_fixture",
    }),
    withVariable({
      ...structuredClone(originalVariable),
      effect: [
        { path: "runs.0.font.size", effect: "$" },
        { path: "runs.0.font.color", effect: "$" },
      ],
    }),
    withVariable(
      structuredClone(originalVariable),
      {
        runs: [
          {
            ...structuredClone(originalElement.runs[0]!),
            font: {
              ...structuredClone(originalElement.runs[0]!.font),
              size: 30,
            },
          },
        ],
      },
    ),
    {
      ...structuredClone(originalElement),
      design_variables: [
        structuredClone(originalVariable),
        structuredClone(originalVariable),
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
