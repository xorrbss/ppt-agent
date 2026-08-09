/**
 * Characterization tests for the deck-level entry points of
 * `template-v2-import`: layout/component extraction, font normalization, the
 * author-info-card pass and the two deck adapters. Expectations mirror current
 * output exactly, quirks included — see the note in
 * `template-v2-import.elements.test.ts`.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { registerAppAliasImports } from "../../../scripts/test-support/node-alias-imports.mjs";

// Pinned so `resolveBackendAssetUrl` output does not depend on the environment.
process.env.NEXT_PUBLIC_FAST_API = "http://backend.test";
registerAppAliasImports();

const {
  adaptGeneratedTemplateV2PresentationToDeck,
  adaptTemplateV2ComponentToElement,
  adaptTemplateV2LayoutToSlide,
  adaptTemplateV2ResponseToDeck,
  extractTemplateV2Layouts,
  extractTemplateV2MergedComponents,
  normalizeTemplateV2Fonts,
  normalizeTemplateV2Slide,
} = await import("./template-v2-import.ts");

function textSlide(text: string, title: string) {
  return {
    title,
    background: "FFFFFF",
    elements: [
      {
        type: "text",
        font: null,
        alignment: null,
        fill: null,
        stroke: null,
        runs: [{ text }],
        max_length: null,
        min_length: null,
      },
    ],
  };
}

test("extractTemplateV2Layouts accepts arrays, `layouts` and nested `layouts.layouts`", () => {
  assert.deepEqual(extractTemplateV2Layouts([{ id: "a" }, "x", null]), [
    { id: "a" },
  ]);
  assert.deepEqual(extractTemplateV2Layouts({ layouts: [{ id: "b" }] }), [
    { id: "b" },
  ]);
  assert.deepEqual(
    extractTemplateV2Layouts({ layouts: { layouts: [{ id: "c" }] } }),
    [{ id: "c" }],
  );
  assert.deepEqual(extractTemplateV2Layouts("nope"), []);
});

test("extractTemplateV2MergedComponents unwraps arrays, `components` and plain maps", () => {
  assert.deepEqual(extractTemplateV2MergedComponents(null), []);
  assert.deepEqual(
    extractTemplateV2MergedComponents({ merged_components: [{ a: 1 }, 2] }),
    [{ a: 1 }],
  );
  assert.deepEqual(
    extractTemplateV2MergedComponents({
      merged_components: { components: [{ b: 1 }] },
    }),
    [{ b: 1 }],
  );
  assert.deepEqual(
    extractTemplateV2MergedComponents({ merged_components: { x: { c: 1 }, y: 2 } }),
    [{ c: 1 }],
  );
  assert.deepEqual(extractTemplateV2MergedComponents({ merged_components: 5 }), []);
});

test("normalizeTemplateV2Fonts drops blank entries and falls back to the given map", () => {
  assert.deepEqual(
    normalizeTemplateV2Fonts({ fonts: { A: "/static/a.woff", "": "x", B: 2 } }),
    { A: "http://backend.test/static/a.woff" },
  );
  assert.deepEqual(normalizeTemplateV2Fonts({}, { C: "https://x/c.woff" }), {
    C: "https://x/c.woff",
  });
  assert.deepEqual(normalizeTemplateV2Fonts({ fonts: {} }, { C: " " }), {});
});

test("author info card widens author_name/date up to the vector background edge", () => {
  const slide = adaptTemplateV2LayoutToSlide({
    id: "layout-7",
    elements: [
      {
        type: "group",
        component_id: "author_info_card",
        position: { x: 0, y: 0 },
        size: { width: 400, height: 100 },
        children: [
          {
            type: "vector",
            points: [
              { x: 0, y: 0 },
              { x: 300, y: 0 },
              { x: 300, y: 100 },
              { x: 0, y: 100 },
            ],
            fill: { color: "EEEEEE" },
          },
          {
            type: "text",
            component_slot: "author_name",
            position: { x: 20, y: 10 },
            size: { width: 50, height: 20 },
            runs: [{ text: "n" }],
          },
          {
            type: "text",
            component_slot: "date",
            position: { x: 20, y: 40 },
            size: { width: 900, height: 20 },
            runs: [{ text: "d" }],
          },
        ],
      },
    ],
  });

  const [group] = slide.elements;
  assert.ok(group.type === "group");
  assert.deepEqual(
    group.children.map((child) => child.size),
    [undefined, { width: 256, height: 20 }, { width: 900, height: 20 }],
  );
  // Already normalized: running the pass again is a no-op.
  assert.deepEqual(normalizeTemplateV2Slide(slide), slide);
});

test("author info card without a vector background widens to the group width", () => {
  const slide = adaptTemplateV2LayoutToSlide({
    id: "layout-8",
    elements: [
      {
        type: "group",
        component_instance_id: "author_info_card:0",
        position: { x: 0, y: 0 },
        size: { width: 400, height: 100 },
        children: [
          {
            type: "text",
            component_slot: "author_name",
            position: { x: 20, y: 10 },
            size: { width: 50, height: 20 },
            runs: [{ text: "n" }],
          },
        ],
      },
    ],
  });

  const [group] = slide.elements;
  assert.ok(group.type === "group");
  assert.deepEqual(
    group.children.map((child) => child.size),
    [{ width: 356, height: 20 }],
  );
});

test("a component with its own frame keeps child coordinates and stretches bare flows", () => {
  assert.deepEqual(
    adaptTemplateV2ComponentToElement(
      {
        id: "cmp",
        description: "d",
        position: { x: 10, y: 20 },
        size: { width: 200, height: 100 },
        design_variables: [],
        elements: [
          { type: "flex", children: [{ type: "text", text: "t" }] },
          {
            type: "text",
            position: { x: 5, y: 5 },
            size: { width: 10, height: 10 },
            text: "u",
          },
        ],
      },
      2,
    ),
    {
      position: { x: 10, y: 20 },
      size: { width: 200, height: 100 },
      component_slot: "cmp",
      component_id: "cmp",
      component_instance_id: "cmp:2",
      component_description: "d",
      type: "group",
      children: [
        {
          position: { x: 0, y: 0 },
          size: { width: 200, height: 100 },
          layout: { grow: 1, shrink: 1 },
          type: "flex",
          direction: "column",
          wrap: null,
          align_items: null,
          justify_content: null,
          padding: null,
          gap: null,
          column_gap: null,
          row_gap: null,
          children: [
            {
              size: { width: 200, height: 100 },
              type: "text",
              font: null,
              alignment: null,
              fill: null,
              stroke: null,
              runs: [{ text: "t" }],
              max_length: null,
              min_length: null,
            },
          ],
          max_children: null,
          min_children: null,
        },
        {
          position: { x: 5, y: 5 },
          size: { width: 10, height: 10 },
          type: "text",
          font: null,
          alignment: null,
          fill: null,
          stroke: null,
          runs: [{ text: "u" }],
          max_length: null,
          min_length: null,
        },
      ],
      max_children: null,
      min_children: null,
    },
  );
});

test("a frameless component derives its frame from element bounds and localizes children", () => {
  assert.deepEqual(
    adaptTemplateV2ComponentToElement({
      elements: [
        {
          type: "text",
          position: { x: 40, y: 50 },
          size: { width: 10, height: 10 },
          text: "a",
        },
        {
          type: "container",
          child: {
            type: "text",
            position: { x: 100, y: 200 },
            size: { width: 5, height: 5 },
            text: "b",
          },
        },
      ],
    }),
    {
      position: { x: 40, y: 50 },
      size: { width: 65, height: 155 },
      component_slot: "component_0",
      component_id: "component_0",
      component_instance_id: "component_0:0",
      type: "group",
      children: [
        {
          position: { x: 0, y: 0 },
          size: { width: 10, height: 10 },
          type: "text",
          font: null,
          alignment: null,
          fill: null,
          stroke: null,
          runs: [{ text: "a" }],
          max_length: null,
          min_length: null,
        },
        {
          size: { width: 5, height: 5 },
          type: "container",
          alignment: null,
          fill: null,
          stroke: null,
          border_radius: null,
          padding: null,
          shadow: null,
          child: {
            position: { x: 60, y: 150 },
            size: { width: 5, height: 5 },
            type: "text",
            font: null,
            alignment: null,
            fill: null,
            stroke: null,
            runs: [{ text: "b" }],
            max_length: null,
            min_length: null,
          },
        },
      ],
      max_children: null,
      min_children: null,
    },
  );
});

test("components without elements or without a derivable frame return null", () => {
  assert.equal(adaptTemplateV2ComponentToElement(null), null);
  assert.equal(adaptTemplateV2ComponentToElement({ elements: [] }), null);
  assert.equal(
    adaptTemplateV2ComponentToElement({ elements: [{ type: "text" }] }),
    null,
  );
});

test("a layout without `elements` falls back to wrapping its components", () => {
  assert.deepEqual(
    adaptTemplateV2LayoutToSlide({
      id: "no-digits",
      components: [
        {
          id: "c1",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "text",
              position: { x: 1, y: 1 },
              size: { width: 2, height: 2 },
              text: "z",
            },
          ],
        },
      ],
    }),
    {
      title: "Slide 1",
      background: "FFFFFF",
      elements: [
        {
          position: { x: 0, y: 0 },
          size: { width: 3, height: 3 },
          component_slot: "c1",
          component_id: "c1",
          component_instance_id: "c1:0",
          type: "group",
          children: [
            {
              position: { x: 1, y: 1 },
              size: { width: 2, height: 2 },
              type: "text",
              font: null,
              alignment: null,
              fill: null,
              stroke: null,
              runs: [{ text: "z" }],
              max_length: null,
              min_length: null,
            },
          ],
          max_children: null,
          min_children: null,
        },
      ],
    },
  );
});

test("adaptTemplateV2ResponseToDeck falls back to raw_layouts and titles from the id digits", () => {
  assert.deepEqual(
    adaptTemplateV2ResponseToDeck({
      name: "N",
      description: "D",
      raw_layouts: [{ id: "raw-2", elements: [{ type: "text", text: "r" }] }],
    }),
    { title: "N", description: "D", slides: [textSlide("r", "Slide 2")] },
  );
  assert.throws(
    () => adaptTemplateV2ResponseToDeck({}),
    /Template response did not include any layouts\./,
  );
});

test("generated presentations prefer per-slide `ui` payloads, sorted by index", () => {
  assert.deepEqual(
    adaptGeneratedTemplateV2PresentationToDeck({
      title: "G",
      slides: [
        { index: 1, ui: { id: "s-9", elements: [{ type: "text", text: "b" }] } },
        { index: 0, ui: { id: "s-1", elements: [{ type: "text", text: "a" }] } },
      ],
    }),
    {
      title: "G",
      description: null,
      slides: [textSlide("a", "Slide 1"), textSlide("b", "Slide 9")],
    },
  );
});

test("generated slides without `ui` look layouts up by id and wrap around otherwise", () => {
  assert.deepEqual(
    adaptGeneratedTemplateV2PresentationToDeck({
      id: "pid",
      layout: { layouts: [{ id: "L1", elements: [{ type: "text", text: "1" }] }] },
      slides: [
        { index: 0, layout: "L1" },
        { index: 1, layout: "missing" },
      ],
    }),
    {
      title: "pid",
      description: null,
      slides: [textSlide("1", "Slide 1"), textSlide("1", "Slide 1")],
    },
  );
});

test("a presentation with layouts but no slides adapts the layouts directly", () => {
  assert.deepEqual(
    adaptGeneratedTemplateV2PresentationToDeck({
      layout: [{ id: "only-3", elements: [{ type: "text", text: "o" }] }],
    }),
    {
      title: "Generated presentation",
      description: null,
      slides: [textSlide("o", "Slide 3")],
    },
  );
  assert.throws(
    () => adaptGeneratedTemplateV2PresentationToDeck({}),
    /Generated presentation did not include template v2 slides\./,
  );
});
