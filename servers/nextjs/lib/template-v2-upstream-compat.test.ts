import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptGeneratedTemplateV2UiToStudio,
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";

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
