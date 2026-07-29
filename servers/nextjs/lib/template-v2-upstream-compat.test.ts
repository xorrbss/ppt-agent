import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  renderTemplateV2GeneralSlideCanvasHtml,
} from "./template-v2-general-renderer.mjs";
import {
  createTemplateV2SlideRenderPlan,
} from "./template-v2-render-plan.mjs";
import {
  adaptGeneratedTemplateV2UiToStudio,
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";
import {
  editTemplateV2VectorPointTopology,
  templateV2CanChangeVectorEllipseShape,
} from "./template-v2-vector.ts";

const pinnedUpstreamElements = JSON.parse(
  readFileSync(
    new URL(
      "../../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json",
      import.meta.url
    ),
    "utf8"
  )
) as {
  upstream_sha: string;
  elements: Array<Record<string, unknown>>;
};

test("round-trips every pinned upstream layouts envelope without data loss", () => {
  const layout = {
    id: "layout-1",
    upstream_extension: { future: true },
    components: [
      {
        id: "component-1",
        elements: [
          {
            type: "text",
            runs: [{ text: "Hello", future_run_field: "kept" }],
            future_element_field: 42,
          },
        ],
      },
    ],
  };
  const values = [
    [layout],
    { layouts: [layout], future_envelope_field: "kept" },
    {
      layouts: {
        layouts: [layout],
        future_nested_envelope_field: "kept",
      },
      future_top_level_field: "kept",
    },
  ];

  for (const value of values) {
    const document = adaptUpstreamTemplateV2LayoutsToStudio(value);
    assert.deepEqual(serializeStudioLayoutsForUpstream(document), value);
  }
});

test("round-trips the pinned upstream vector after one proven topology edit", () => {
  assert.equal(
    pinnedUpstreamElements.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83"
  );
  const fixtureVector = pinnedUpstreamElements.elements.find(
    (element) => element.type === "vector"
  );
  assert.ok(fixtureVector);

  const source = {
    layouts: [{
      id: "upstream-vector-roundtrip",
      components: [{
        id: "vector-component",
        position: { x: 0, y: 0 },
        elements: [structuredClone(fixtureVector)],
      }],
    }],
    future_envelope_field: "kept",
  };
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(document.studioLayouts);
  const layout = (edited.layouts as Array<Record<string, unknown>>)[0];
  const component = (
    layout.components as Array<Record<string, unknown>>
  )[0];
  const vector = (
    component.elements as Array<Record<string, unknown>>
  )[0];

  // The pinned record proves polygon topology preservation, but its authored
  // closed path does not prove a semantics-safe ellipse transition.
  assert.equal(templateV2CanChangeVectorEllipseShape(vector), false);
  const topologyEdit = editTemplateV2VectorPointTopology(vector, {
    type: "add-after",
    index: 0,
  });
  assert.ok(topologyEdit);
  vector.points = topologyEdit.points;
  if (topologyEdit.cornerRadii !== null) {
    vector.corner_radii = topologyEdit.cornerRadii;
  }

  const wire = serializeStudioLayoutsForUpstream(document, edited) as {
    layouts: Array<Record<string, unknown>>;
    future_envelope_field: string;
  };
  assert.equal(wire.future_envelope_field, "kept");
  const savedLayout = wire.layouts[0];
  const savedComponent = (
    savedLayout.components as Array<Record<string, unknown>>
  )[0];
  const savedVector = (
    savedComponent.elements as Array<Record<string, unknown>>
  )[0];
  assert.deepEqual(savedVector, {
    ...fixtureVector,
    points: [
      { x: 0, y: 0 },
      { x: 50, y: 40 },
      { x: 100, y: 80 },
    ],
  });
  assert.deepEqual(
    (source.layouts[0].components[0].elements[0] as Record<string, unknown>)
      .points,
    fixtureVector.points
  );

  const reentered = adaptUpstreamTemplateV2LayoutsToStudio(wire);
  assert.deepEqual(
    serializeStudioLayoutsForUpstream(reentered),
    wire
  );
  const plan = createTemplateV2SlideRenderPlan({
    id: "upstream-vector-roundtrip",
    ui: savedLayout,
  });
  assert.deepEqual(plan.components[0].elements[0].vector, {
    shape: "polygon",
    frame: { x: 0, y: 0, width: 100, height: 80 },
    points: [
      { x: 0, y: 0 },
      { x: 50, y: 40 },
      { x: 100, y: 80 },
    ],
    closed: true,
    tension: null,
    segments: null,
  });
  assert.match(
    renderTemplateV2GeneralSlideCanvasHtml({
      id: "upstream-vector-roundtrip",
      ui: savedLayout,
    }),
    /<polygon /
  );
});

test("merges Studio edits while preserving unknown upstream fields and shape", () => {
  const source = {
    layouts: {
      layouts: [
        {
          id: "layout-1",
          future_layout_field: "kept",
          components: [],
        },
      ],
      future_envelope_field: "kept",
    },
    future_top_level_field: "kept",
  };
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(document.studioLayouts);
  const first = (edited.layouts as Array<Record<string, unknown>>)[0];
  first.description = "Edited locally";

  assert.deepEqual(serializeStudioLayoutsForUpstream(document, edited), {
    layouts: {
      layouts: [
        {
          id: "layout-1",
          description: "Edited locally",
          future_layout_field: "kept",
          components: [],
        },
      ],
      future_envelope_field: "kept",
    },
    future_top_level_field: "kept",
  });
});

test("adapts generated slide.ui layouts in stable index order", () => {
  const document = adaptGeneratedTemplateV2UiToStudio({
    id: "presentation-1",
    slides: [
      {
        index: 2,
        ui: { id: "layout-2", future_slide_ui_field: "kept" },
      },
      {
        index: 1,
        ui: { id: "layout-1", future_slide_ui_field: "also-kept" },
      },
      { index: 3, ui: null },
    ],
  });

  assert.deepEqual(document.studioLayouts, {
    layouts: [
      { id: "layout-1", future_slide_ui_field: "also-kept" },
      { id: "layout-2", future_slide_ui_field: "kept" },
    ],
  });
});

test("fails closed for malformed layouts instead of silently dropping values", () => {
  assert.throws(
    () =>
      adaptUpstreamTemplateV2LayoutsToStudio({
        layouts: [{ id: "valid" }, "invalid"],
      }),
    /template_v2_upstream_layouts_invalid/
  );
  assert.throws(
    () => adaptGeneratedTemplateV2UiToStudio({ slides: [{ ui: null }] }),
    /template_v2_upstream_layouts_invalid/
  );
});
