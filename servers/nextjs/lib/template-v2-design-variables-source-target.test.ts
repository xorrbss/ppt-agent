import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applyTemplateV2DirectComponentDesignVariableOption,
  inspectTemplateV2ComponentDesignVariableEditCapability,
  inspectTemplateV2DesignVariablesContract,
} from "./template-v2-design-variables-contract.ts";
import { renderTemplateV2GeneralPresentationHtml } from "./template-v2-general-renderer.mjs";
import { createTemplateV2SlideRenderPlan } from "./template-v2-render-plan.mjs";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";

type FixtureComponent = {
  design_variables: Array<Record<string, unknown>>;
  elements: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

type FixtureLayout = {
  components: FixtureComponent[];
  [key: string]: unknown;
};

type FixtureWire = {
  layouts: {
    layouts: FixtureLayout[];
    [key: string]: unknown;
  };
  [key: string]: unknown;
};

type SourceTargetFixture = {
  contract: {
    upstream_commit: string;
    witness_kind: string;
    upstream_source: string;
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
      "./fixtures/template-v2-upstream-design-variables-source-target-e1ad7e7.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as SourceTargetFixture;

const directCapability = {
  activation: "direct-option",
  variableIndex: 0,
  name: "scale_variant",
  optionIndex: 0,
  targets: [
    "elements.0.size.width",
    "elements.1.runs.0.font.size",
  ],
};

test("upstream source/target Design Variable survives import, edit, serialize, JSON reload, and render", () => {
  assert.equal(fixture.contract.upstream_commit, "e1ad7e7f");
  assert.equal(fixture.contract.witness_kind, "upstream-unit-test-derived");
  assert.equal(
    fixture.contract.upstream_source,
    "servers/fastapi/tests/unit/test_templates_v2_generation.py",
  );
  assert.deepEqual(fixture.contract.reviewed_slice, {
    variable_name: "scale_variant",
    variable_type: "object",
    option_count: 1,
    option: { width: 240, font_size: 28 },
    effects: [
      { source: "$.width", target: "elements.0.size.width" },
      {
        source: "$.font_size",
        target: "elements.1.runs.0.font.size",
      },
    ],
    initial_width: 200,
    initial_font_size: 20,
  });

  const originalWire = structuredClone(fixture.wire);
  assert.equal(
    fixture.wire.layouts.layouts[0]!.components[0]!.elements[0]!.type,
    "rectangle",
  );
  const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
  assert.deepEqual(document.normalizations, [
    {
      layoutId: "source-target-design-variable",
      componentId: "metric_card",
      elementIndex: 0,
      upstreamType: "rectangle",
      studioType: "container",
    },
  ]);
  const studioLayouts = structuredClone(document.studioLayouts) as {
    layouts: FixtureLayout[];
  };
  const originalComponent = studioLayouts.layouts[0]!.components[0]!;
  assert.equal(originalComponent.elements[0]!.type, "container");
  assert.deepEqual(
    inspectTemplateV2ComponentDesignVariableEditCapability(originalComponent),
    directCapability,
  );

  const editedComponent =
    applyTemplateV2DirectComponentDesignVariableOption(originalComponent, {
      variableIndex: fixture.edit.variable_index,
      optionIndex: fixture.edit.option_index,
    });
  studioLayouts.layouts[0]!.components[0] =
    editedComponent as FixtureComponent;

  assert.notEqual(editedComponent, originalComponent);
  assert.equal(
    (
      (originalComponent.elements[0]!.size as Record<string, unknown>)
    ).width,
    200,
  );
  assert.equal(
    (
      (
        (originalComponent.elements[1]!.runs as Array<
          Record<string, unknown>
        >)[0]!.font as Record<string, unknown>
      ).size
    ),
    20,
  );
  assert.deepEqual(
    inspectTemplateV2ComponentDesignVariableEditCapability(editedComponent),
    { activation: "retention-only" },
  );

  const saved = serializeStudioLayoutsForUpstream(
    document,
    studioLayouts,
  ) as FixtureWire;
  const expectedWire = structuredClone(fixture.wire);
  const expectedComponent =
    expectedWire.layouts.layouts[0]!.components[0]!;
  (expectedComponent.elements[0]!.size as Record<string, unknown>).width = 240;
  (
    (
      (expectedComponent.elements[1]!.runs as Array<Record<string, unknown>>)[0]!
        .font as Record<string, unknown>
    )
  ).size = 28;
  assert.deepEqual(saved, expectedWire);
  assert.deepEqual(
    saved.layouts.layouts[0]!.components[0]!.design_variables,
    fixture.wire.layouts.layouts[0]!.components[0]!.design_variables,
  );

  const persistedWire = JSON.parse(JSON.stringify(saved)) as FixtureWire;
  const reloadedDocument =
    adaptUpstreamTemplateV2LayoutsToStudio(persistedWire);
  const reloadedWire = serializeStudioLayoutsForUpstream(
    reloadedDocument,
  ) as FixtureWire;
  assert.deepEqual(reloadedWire, expectedWire);

  const reloadedLayout = (
    reloadedDocument.studioLayouts.layouts as FixtureLayout[]
  )[0]!;
  const reloadedComponent = reloadedLayout.components[0]!;
  assert.equal(reloadedComponent.elements[0]!.type, "container");
  assert.deepEqual(
    inspectTemplateV2ComponentDesignVariableEditCapability(reloadedComponent),
    { activation: "retention-only" },
  );

  const slide = { id: "source-target-design-variable", ui: reloadedLayout };
  const contract = inspectTemplateV2DesignVariablesContract(slide);
  assert.equal(contract.activation, "retention-only");
  assert.equal(contract.toolbarSafe, false);
  assert.deepEqual(contract.declarations, [
    {
      path: ["ui", "components", 0, "design_variables"],
      bindings:
        fixture.wire.layouts.layouts[0]!.components[0]!.design_variables,
    },
  ]);
  assert.doesNotThrow(() => createTemplateV2SlideRenderPlan(slide));
  const html = renderTemplateV2GeneralPresentationHtml({
    version: "v2-standard",
    mode: "template",
    slides: [slide],
  });
  assert.match(html, /Metric/);
  assert.match(html, /width:240px/);
  assert.match(html, /font-size:28px/);
  assert.deepEqual(fixture.wire, originalWire);
});

test("pinned Design Variable normalization fails closed if its Studio type is changed", () => {
  const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
  const studioLayouts = structuredClone(document.studioLayouts) as {
    layouts: FixtureLayout[];
  };
  studioLayouts.layouts[0]!.components[0]!.elements[0]!.type = "shape";
  assert.throws(
    () => serializeStudioLayoutsForUpstream(document, studioLayouts),
    /template_v2_upstream_normalization_invalid/,
  );
});

test("pinned Design Variable normalization rejects unproven post-import mutation", () => {
  const mutationCases = [
    (component: FixtureComponent) => {
      const variable = component.design_variables[0]!;
      const effects = variable.effect as Array<Record<string, unknown>>;
      effects[0]!.source = "$.width * 2";
    },
    (component: FixtureComponent) => {
      component.elements[0] = {
        type: "container",
        future: "replacement",
      };
    },
    (component: FixtureComponent) => {
      (component.elements[0]!.size as Record<string, unknown>).width = 240;
    },
    (component: FixtureComponent) => {
      component.description = "Different component";
    },
  ];

  mutationCases.forEach((mutate) => {
    const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
    const studioLayouts = structuredClone(document.studioLayouts) as {
      layouts: FixtureLayout[];
    };
    mutate(studioLayouts.layouts[0]!.components[0]!);
    assert.throws(
      () => serializeStudioLayoutsForUpstream(document, studioLayouts),
      /template_v2_upstream_normalization_invalid/,
    );
  });
});

test("pinned Design Variable normalization follows stable ids and rejects ambiguity", () => {
  const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
  const studioLayouts = structuredClone(document.studioLayouts) as {
    layouts: FixtureLayout[];
  };
  const layout = studioLayouts.layouts[0]!;
  layout.components.unshift({
    id: "unrelated",
    design_variables: [],
    elements: [{ type: "container", future: "preserve" }],
  });
  const saved = serializeStudioLayoutsForUpstream(
    document,
    studioLayouts,
  ) as FixtureWire;
  assert.equal(saved.layouts.layouts[0]!.components[0]!.id, "unrelated");
  assert.deepEqual(
    saved.layouts.layouts[0]!.components[0]!.elements[0],
    { type: "container", future: "preserve" },
  );
  assert.equal(
    saved.layouts.layouts[0]!.components[1]!.elements[0]!.type,
    "rectangle",
  );

  layout.components.push(structuredClone(layout.components[1]!));
  assert.throws(
    () => serializeStudioLayoutsForUpstream(document, studioLayouts),
    /template_v2_upstream_normalization_invalid/,
  );
});

test("near-match upstream payloads remain unnormalized and retention-only", () => {
  const nearMatches = [
    (component: FixtureComponent) => {
      component.description = "Different component";
    },
    (component: FixtureComponent) => {
      (component.elements[1]!.position as Record<string, unknown>).x = 21;
    },
    (component: FixtureComponent) => {
      component.elements[1]!.fixed = true;
    },
  ];

  nearMatches.forEach((mutate) => {
    const wire = structuredClone(fixture.wire);
    const component = wire.layouts.layouts[0]!.components[0]!;
    mutate(component);
    const document = adaptUpstreamTemplateV2LayoutsToStudio(wire);
    assert.deepEqual(document.normalizations, []);
    const retained = (
      document.studioLayouts.layouts as FixtureLayout[]
    )[0]!.components[0]!;
    assert.equal(retained.elements[0]!.type, "rectangle");
    assert.deepEqual(
      inspectTemplateV2ComponentDesignVariableEditCapability(retained),
      { activation: "retention-only" },
    );
    assert.deepEqual(serializeStudioLayoutsForUpstream(document), wire);
  });
});

test("pinned source/target edit requires exact component and nested element witnesses", () => {
  const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
  const original = (
    document.studioLayouts.layouts as FixtureLayout[]
  )[0]!.components[0]!;

  assert.deepEqual(original.future_component_field, { retain: "exactly" });
  assert.deepEqual(
    inspectTemplateV2ComponentDesignVariableEditCapability(original),
    directCapability,
  );

  const mutations: Array<{
    name: string;
    mutate: (component: FixtureComponent) => void;
  }> = [
    {
      name: "component id value",
      mutate: (component) => {
        component.id = "different";
      },
    },
    {
      name: "component description value",
      mutate: (component) => {
        component.description = "Different";
      },
    },
    {
      name: "component position value",
      mutate: (component) => {
        (component.position as Record<string, unknown>).x = 1;
      },
    },
    {
      name: "component position extra key",
      mutate: (component) => {
        (
          component.position as Record<string, unknown>
        ).future_position_semantics = true;
      },
    },
    {
      name: "component size value",
      mutate: (component) => {
        (component.size as Record<string, unknown>).width = 201;
      },
    },
    {
      name: "component size extra key",
      mutate: (component) => {
        (
          component.size as Record<string, unknown>
        ).future_size_semantics = true;
      },
    },
    {
      name: "container extra key",
      mutate: (component) => {
        component.elements[0]!.future_container_semantics = true;
      },
    },
    {
      name: "container position value",
      mutate: (component) => {
        (
          component.elements[0]!.position as Record<string, unknown>
        ).x = 1;
      },
    },
    {
      name: "container position extra key",
      mutate: (component) => {
        (
          component.elements[0]!.position as Record<string, unknown>
        ).future_position_semantics = true;
      },
    },
    {
      name: "container size value",
      mutate: (component) => {
        (component.elements[0]!.size as Record<string, unknown>).height = 101;
      },
    },
    {
      name: "container size extra key",
      mutate: (component) => {
        (
          component.elements[0]!.size as Record<string, unknown>
        ).future_size_semantics = true;
      },
    },
    {
      name: "text extra key",
      mutate: (component) => {
        component.elements[1]!.future_text_semantics = true;
      },
    },
    {
      name: "text position value",
      mutate: (component) => {
        (
          component.elements[1]!.position as Record<string, unknown>
        ).y = 21;
      },
    },
    {
      name: "text position extra key",
      mutate: (component) => {
        (
          component.elements[1]!.position as Record<string, unknown>
        ).future_position_semantics = true;
      },
    },
    {
      name: "text size value",
      mutate: (component) => {
        (component.elements[1]!.size as Record<string, unknown>).width = 161;
      },
    },
    {
      name: "text size extra key",
      mutate: (component) => {
        (
          component.elements[1]!.size as Record<string, unknown>
        ).future_size_semantics = true;
      },
    },
    {
      name: "text fixed value",
      mutate: (component) => {
        component.elements[1]!.fixed = true;
      },
    },
    {
      name: "text name value",
      mutate: (component) => {
        component.elements[1]!.name = "different";
      },
    },
    {
      name: "text minimum length value",
      mutate: (component) => {
        component.elements[1]!.min_length = 11;
      },
    },
    {
      name: "text maximum length value",
      mutate: (component) => {
        component.elements[1]!.max_length = 21;
      },
    },
    {
      name: "run extra key",
      mutate: (component) => {
        const runs = component.elements[1]!.runs as Array<
          Record<string, unknown>
        >;
        runs[0]!.future_run_semantics = true;
      },
    },
    {
      name: "run text value",
      mutate: (component) => {
        const runs = component.elements[1]!.runs as Array<
          Record<string, unknown>
        >;
        runs[0]!.text = "Different";
      },
    },
    {
      name: "font extra key",
      mutate: (component) => {
        const runs = component.elements[1]!.runs as Array<
          Record<string, unknown>
        >;
        (runs[0]!.font as Record<string, unknown>).family = "Inter";
      },
    },
    {
      name: "font size value",
      mutate: (component) => {
        const runs = component.elements[1]!.runs as Array<
          Record<string, unknown>
        >;
        (runs[0]!.font as Record<string, unknown>).size = 21;
      },
    },
  ];

  mutations.forEach(({ name, mutate }) => {
    const component = structuredClone(original);
    mutate(component);
    assert.deepEqual(
      inspectTemplateV2ComponentDesignVariableEditCapability(component),
      { activation: "retention-only" },
      name,
    );
    assert.throws(
      () =>
        applyTemplateV2DirectComponentDesignVariableOption(component, {
          variableIndex: 0,
          optionIndex: 0,
        }),
      /template_v2_design_variables_unproven_component_edit/,
      name,
    );
  });
});

test("unproven source/target expressions and paths remain retention-only", () => {
  const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
  const original = (
    document.studioLayouts.layouts as FixtureLayout[]
  )[0]!.components[0]!;
  const variable = original.design_variables[0]!;
  const withVariable = (candidate: Record<string, unknown>) => ({
    ...structuredClone(original),
    design_variables: [candidate],
  });
  const withEffects = (effects: Array<Record<string, unknown>>) =>
    withVariable({
      ...structuredClone(variable),
      effect: effects,
    });

  const unproven = [
    withEffects([
      { source: "$.width * 2", target: "elements.0.size.width" },
      { source: "$.font_size", target: "elements.1.runs.0.font.size" },
    ]),
    withEffects([
      { source: "$.width", target: "rectangle.size.width" },
      { source: "$.font_size", target: "elements.1.runs.0.font.size" },
    ]),
    withEffects([
      { source: "$.width", target: "elements.1.runs.0.font.size" },
      { source: "$.font_size", target: "elements.0.size.width" },
    ]),
    withEffects([
      {
        source: "$.width",
        target: "elements.0.size.width",
        future_effect: true,
      },
      { source: "$.font_size", target: "elements.1.runs.0.font.size" },
    ]),
    withVariable({
      ...structuredClone(variable),
      options: [{ width: 240, font_size: 28, future_option: true }],
    }),
    withVariable({
      ...structuredClone(variable),
      future_binding: true,
    }),
    {
      ...structuredClone(original),
      elements: [
        {
          ...structuredClone(original.elements[0]!),
          size: {
            ...(original.elements[0]!.size as Record<string, unknown>),
            width: 201,
          },
        },
        structuredClone(original.elements[1]!),
      ],
    },
  ];

  unproven.forEach((component) => {
    assert.deepEqual(
      inspectTemplateV2ComponentDesignVariableEditCapability(component),
      { activation: "retention-only" },
    );
    assert.throws(
      () =>
        applyTemplateV2DirectComponentDesignVariableOption(component, {
          variableIndex: 0,
          optionIndex: 0,
        }),
      /template_v2_design_variables_unproven_component_edit/,
    );
  });

  assert.throws(
    () =>
      applyTemplateV2DirectComponentDesignVariableOption(original, {
        variableIndex: 0,
        optionIndex: 1,
      }),
    /template_v2_design_variables_invalid_option_index/,
  );
});

test("pinned source/target edit rejects malformed or widened selections", () => {
  const document = adaptUpstreamTemplateV2LayoutsToStudio(fixture.wire);
  const original = (
    document.studioLayouts.layouts as FixtureLayout[]
  )[0]!.components[0]!;
  const malformedSelections: unknown[] = [
    null,
    [],
    {},
    { variableIndex: 0 },
    { variableIndex: "0", optionIndex: 0 },
    { variableIndex: 0, optionIndex: 0, future_selection_semantics: true },
  ];

  malformedSelections.forEach((selection) => {
    assert.throws(
      () =>
        applyTemplateV2DirectComponentDesignVariableOption(
          original,
          selection,
        ),
      /template_v2_design_variables_invalid_selection/,
    );
    assert.equal(
      (original.elements[0]!.size as Record<string, unknown>).width,
      200,
    );
    const runs = original.elements[1]!.runs as Array<Record<string, unknown>>;
    assert.equal(
      (runs[0]!.font as Record<string, unknown>).size,
      20,
    );
  });
});
