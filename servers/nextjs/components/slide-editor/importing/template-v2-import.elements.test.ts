/**
 * Characterization tests: they pin what `template-v2-import` actually produces
 * today, quirks included (e.g. `letter_spacing: 0` forced onto every font,
 * `shape: undefined` left on vectors, a dropped vector replaced by the
 * invisible fallback element). They are a regression net for refactors, not a
 * statement of intended behaviour — do not "fix" an expectation without first
 * confirming the production change that motivates it.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { registerAppAliasImports } from "../../../scripts/test-support/node-alias-imports.mjs";

// Pinned so `resolveBackendAssetUrl` output does not depend on the environment.
process.env.NEXT_PUBLIC_FAST_API = "http://backend.test";
registerAppAliasImports();

const { adaptTemplateV2LayoutToSlide, withEqualTemplateV2FlowChildSizes } =
  await import("./template-v2-import.ts");

function elementsOf(elements: unknown[]) {
  return adaptTemplateV2LayoutToSlide({ id: "layout-1", elements }).elements;
}

const INVISIBLE_FALLBACK = {
  type: "vector",
  points: [
    { x: 0, y: 0 },
    { x: 1, y: 0 },
    { x: 1, y: 1 },
    { x: 0, y: 1 },
  ],
  closed: true,
  fill: { color: "FFFFFF" },
  opacity: 0,
};

test("text element clamps, truncates and normalizes every decorated field", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "text",
        name: "heading",
        position: { x: "12.34567", y: -4 },
        size: { width: 5000, height: 20 },
        rotation: 400,
        opacity: 2,
        decorative: true,
        font: {
          family: "Inter",
          size: 900,
          color: "#AABBCC",
          font_weight: 700,
          text_decoration: "underline",
          line_height: 9,
          letter_spacing: 4,
          opacity: 0.5,
        },
        alignment: { horizontal: "center", vertical: "bottom", other: "x" },
        fill: { color: "112233", opacity: 5 },
        stroke: { color: "445566", width: 40, dash: ["2", -1, 3] },
        runs: [
          { text: "a", font: { family: "X" } },
          { text: "" },
          { notext: 1 },
          "raw",
        ],
        max_length: 10,
        min_length: 2,
        layout: { grow: 99, column_span: 40, align_self: "nope" },
        design_variables: [
          {
            name: "v",
            type: "color",
            options: ["#fff", { a: 1 }, () => 1],
            effect: [
              { path: "p", effect: "e" },
              { path: "", effect: "e" },
            ],
          },
          { name: "", options: [1], effect: [{ path: "p", effect: "e" }] },
        ],
        component_id: "cid",
        component_instance_id: "ciid",
        component_description: "desc",
        shadow: { color: "000000", blur: -3, offsetX: "4", offsetY: 5 },
      },
    ]),
    [
      {
        decorative: true,
        position: { x: 12.3457, y: -4 },
        size: { width: 1280, height: 20 },
        rotation: 360,
        opacity: 1,
        component_slot: "heading",
        design_variables: [
          {
            name: "v",
            type: "color",
            options: ["#fff", { a: 1 }],
            effect: [{ path: "p", effect: "e" }],
          },
        ],
        component_id: "cid",
        component_instance_id: "ciid",
        component_description: "desc",
        shadow: {
          color: "000000",
          blur: 0,
          offsetX: "4",
          offsetY: 5,
          opacity: 0.2,
          offset_x: 4,
          offset_y: 5,
        },
        layout: { grow: 12, column_span: 12 },
        type: "text",
        font: {
          family: "Inter",
          size: 360,
          color: "#AABBCC",
          bold: true,
          underline: true,
          line_height: 2.2,
          letter_spacing: 4,
          opacity: 0.5,
        },
        alignment: { horizontal: "center", vertical: "bottom" },
        fill: { color: "112233", opacity: 1 },
        stroke: { color: "445566", width: 8, dash: [2, 3], opacity: 1 },
        runs: [{ text: "a", font: { family: "X", letter_spacing: 0 } }],
        max_length: 10,
        min_length: 2,
      },
    ],
  );
});

test("text without runs falls back to a single 'Text' run and line_height 1", () => {
  assert.deepEqual(elementsOf([{ type: "text", font: { size: 1 }, runs: [] }]), [
    {
      type: "text",
      font: { size: 6, letter_spacing: 0, line_height: 1 },
      alignment: null,
      fill: null,
      stroke: null,
      runs: [{ text: "Text" }],
      max_length: null,
      min_length: null,
    },
  ]);
});

test("math keeps latex and defaults display_mode to true", () => {
  assert.deepEqual(
    elementsOf([{ type: "math", latex: "x^2", display_mode: false }]),
    [
      {
        type: "math",
        latex: "x^2",
        display_mode: false,
        font: null,
        alignment: null,
        max_length: null,
        min_length: null,
      },
    ],
  );
  assert.deepEqual(elementsOf([{ type: "math" }]), [
    {
      type: "math",
      latex: "",
      display_mode: true,
      font: null,
      alignment: null,
      max_length: null,
      min_length: null,
    },
  ]);
});

test("container without a size infers one from the padded child", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "container",
        padding: { top: 4, right: 6, bottom: -2, left: "8" },
        border_radius: { tl: 999, tr: -5, bl: 2, br: 3 },
        fill: { color: "FFFFFF" },
        child: {
          type: "text",
          size: { width: 100, height: 50 },
          position: { x: 0, y: 0 },
          text: "c",
        },
      },
    ]),
    [
      {
        size: { width: 114, height: 54 },
        type: "container",
        alignment: null,
        fill: { color: "FFFFFF", opacity: 1 },
        stroke: null,
        border_radius: { tl: 128, tr: 0, bl: 2, br: 3 },
        padding: { top: 4, right: 6, bottom: 0, left: 8 },
        shadow: null,
        child: {
          position: { x: 0, y: 0 },
          size: { width: 100, height: 50 },
          type: "text",
          font: null,
          alignment: null,
          fill: null,
          stroke: null,
          runs: [{ text: "c" }],
          max_length: null,
          min_length: null,
        },
      },
    ],
  );
});

test("image resolves backend assets and mirrors `name` into component_slot", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "image",
        data: "/static/a.png",
        name: "pic",
        fit: "cover",
        focus_x: 0.2,
        focus_y: "0.4",
        crop_scale: 2,
        clipPath: "circle()",
        color: "112233",
        is_icon: true,
        icon_type: "duotone",
        flip_h: true,
        flip_v: false,
        border_radius: { tl: 1 },
      },
    ]),
    [
      {
        component_slot: "pic",
        type: "image",
        flip_h: true,
        flip_v: false,
        data: "http://backend.test/static/a.png",
        name: "pic",
        fit: "cover",
        focus_x: 0.2,
        focus_y: 0.4,
        crop_scale: 2,
        border_radius: { tl: 1, tr: 0, bl: 0, br: 0 },
        clippath: "circle()",
        color: "112233",
        is_icon: true,
        icon_type: "duotone",
      },
    ],
  );
  assert.deepEqual(elementsOf([{ type: "image" }]), [
    {
      type: "image",
      flip_h: null,
      flip_v: null,
      data: null,
      name: null,
      fit: null,
      focus_x: null,
      focus_y: null,
      crop_scale: null,
      border_radius: null,
      clippath: null,
      color: null,
      is_icon: null,
      icon_type: null,
    },
  ]);
});

test("text-list renders markdown, merges run arrays and drops empty items", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "text-list",
        marker: "number",
        font: { family: "Inter" },
        items: [
          "plain **bold** text",
          { text: "obj" },
          [{ text: "run1" }, { text: "run2" }],
          [],
          { nothing: true },
        ],
        max_items: 3,
        min_items: 1,
        max_item_length: 20,
        min_item_length: 1,
      },
    ]),
    [
      {
        type: "text-list",
        font: { family: "Inter", letter_spacing: 0 },
        marker: "number",
        items: [
          [
            { text: "plain " },
            { text: "bold", font: { bold: true } },
            { text: " text" },
          ],
          [{ text: "obj" }],
          [{ text: "run1run2" }],
        ],
        max_items: 3,
        min_items: 1,
        max_item_length: 20,
        min_item_length: 1,
      },
    ],
  );
  assert.deepEqual(elementsOf([{ type: "text-list", items: [] }]), [
    {
      type: "text-list",
      font: null,
      marker: null,
      items: [[{ text: "List item" }]],
      max_items: null,
      min_items: null,
      max_item_length: null,
      min_item_length: null,
    },
  ]);
});

test("table coerces scalar cells and replaces non-array rows with an empty cell", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "table",
        columns: ["A", 2, { text: "C" }],
        rows: [
          [{ runs: [{ text: "r1" }, { text: "r2" }] }, { text: { text: "n" } }],
          "not-an-array",
          [],
        ],
        font: { family: "Inter" },
        max_columns: 4,
        min_columns: 1,
        max_rows: 5,
        min_rows: 1,
      },
    ]),
    [
      {
        type: "table",
        font: { family: "Inter", letter_spacing: 0 },
        columns: [
          { runs: [{ text: "A" }] },
          { runs: [{ text: "2" }] },
          { runs: [{ text: "C" }] },
        ],
        rows: [
          [{ runs: [{ text: "r1" }, { text: "r2" }] }, { runs: [{ text: "n" }] }],
          [{ runs: [] }],
          [{ runs: [] }],
        ],
        max_columns: 4,
        min_columns: 1,
        max_rows: 5,
        min_rows: 1,
      },
    ],
  );
  assert.deepEqual(elementsOf([{ type: "table" }]), [
    {
      type: "table",
      font: null,
      columns: [{ runs: [] }],
      rows: [[{ runs: [] }]],
      max_columns: null,
      min_columns: null,
      max_rows: null,
      min_rows: null,
    },
  ]);
});

test("vector keeps unknown fields, drops position/size and clamps the curve", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "vector",
        shape: "polygon",
        extra_field: "kept",
        position: { x: 3, y: 3 },
        size: { width: 9, height: 9 },
        points: [
          { x: 0, y: 0, meta: 1 },
          { x: "10", y: 0 },
          { x: 10, y: 10 },
          { bad: true },
        ],
        corner_radii: [1, "2", -3, "x"],
        curve: { type: "SMOOTH", tension: 5, segments: 200 },
        fill: { color: "FF0000" },
        stroke: { color: "00FF00", width: 3 },
      },
    ]),
    [
      {
        extra_field: "kept",
        type: "vector",
        shape: "polygon",
        points: [
          { x: 0, y: 0, meta: 1 },
          { x: 10, y: 0 },
          { x: 10, y: 10 },
        ],
        closed: true,
        corner_radii: [1, 2, 0],
        curve: { type: "smooth", tension: 1, segments: 96 },
        fill: { color: "FF0000", opacity: 1 },
        stroke: { color: "00FF00", width: 3, opacity: 1 },
        shadow: null,
      },
    ],
  );
});

test("vector is dropped when invisible or under three points, leaving the fallback", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "vector",
        points: [
          { x: 0, y: 0 },
          { x: 1, y: 1 },
        ],
        opacity: 0,
        fill: { color: "FF0000" },
      },
    ]),
    [INVISIBLE_FALLBACK],
  );
  assert.deepEqual(
    elementsOf([
      { type: "vector", points: [{ x: 0, y: 0 }], fill: { color: "FF0000" } },
    ]),
    [INVISIBLE_FALLBACK],
  );
});

test("vector `closed` accepts strings and non-smooth curves become null", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "vector",
        closed: "FALSE",
        points: [
          { x: 0, y: 0 },
          { x: 1, y: 0 },
          { x: 1, y: 1 },
        ],
        fill: { color: "FF0000" },
        curve: { type: "linear" },
      },
    ]),
    [
      {
        type: "vector",
        shape: undefined,
        points: [
          { x: 0, y: 0 },
          { x: 1, y: 0 },
          { x: 1, y: 1 },
        ],
        closed: false,
        corner_radii: null,
        curve: null,
        fill: { color: "FF0000", opacity: 1 },
        stroke: null,
        shadow: null,
      },
    ],
  );
});

test("a stage-sized filled vector becomes the slide background", () => {
  const slide = adaptTemplateV2LayoutToSlide({
    id: "layout-1",
    elements: [
      {
        type: "vector",
        points: [
          { x: 0, y: 0 },
          { x: 1280, y: 0 },
          { x: 1280, y: 720 },
          { x: 0, y: 720 },
        ],
        fill: { color: "123456" },
      },
    ],
  });
  assert.equal(slide.background, "123456");
});

test("chart keeps one series for pie, clamps values and derives chart data", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "chart",
        chart_type: "pie",
        categories: ["A", { label: "B" }, ""],
        series: [
          { name: "S1", values: [1, "2", 9e9, null] },
          { name: "S2", values: [4, 5] },
          { values: [] },
        ],
        colors: ["#FF0000", "bad", "00FF00"],
        data_labels: "mid",
        legend: true,
        title: "T",
        title_color: "AABBCC",
        legendColor: "DDEEFF",
        axis_color: "000000",
        grid_color: "111111",
        x_axis: true,
        y_axis: false,
        x_axis_grid: true,
        y_axis_grid: false,
        x_axis_title: "X",
        y_axis_title: "Y",
        source: "src",
      },
    ]),
    [
      {
        type: "chart",
        chart_type: "pie",
        data: [
          { label: "A", value: 1, color: "#FF0000" },
          { label: "B", value: 2, color: "00FF00" },
          { label: "Item 3", value: 1000000, color: "#FF0000" },
        ],
        title: "T",
        title_color: "AABBCC",
        legend_color: "DDEEFF",
        color: "#FF0000",
        axis_color: "000000",
        grid_color: "111111",
        data_labels: "mid",
        legend: true,
        colors: ["#FF0000", "00FF00"],
        x_axis: true,
        y_axis: false,
        x_axis_grid: true,
        y_axis_grid: false,
        x_axis_title: "X",
        y_axis_title: "Y",
        categories: ["A", "B", "Item 3"],
        series: [{ name: "S1", values: [1, 2, 1000000] }],
        source: "src",
      },
    ],
  );
});

test("empty chart defaults to a bar with placeholder data and dataLabels true means 'top'", () => {
  assert.deepEqual(
    elementsOf([{ type: "chart", dataLabels: true, series: [], categories: [] }]),
    [
      {
        type: "chart",
        chart_type: "bar",
        data: [{ label: "Data", value: 0 }],
        title: null,
        title_color: null,
        legend_color: null,
        color: null,
        axis_color: null,
        grid_color: null,
        data_labels: "top",
        legend: null,
        colors: [],
        x_axis: null,
        y_axis: null,
        x_axis_grid: null,
        y_axis_grid: null,
        x_axis_title: null,
        y_axis_title: null,
        categories: [],
        series: [],
        source: null,
      },
    ],
  );
});

test("infographic nudges max_value when it equals min_value", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "infographic",
        data: { type: "progress_bar", min_value: 10, max_value: 10, value: 3 },
        colors: ["#ABCDEF", "nope"],
      },
    ]),
    [
      {
        type: "infographic",
        data: { type: "progress_bar", min_value: 10, max_value: 11, value: 3 },
        colors: ["#ABCDEF"],
      },
    ],
  );
  assert.deepEqual(elementsOf([{ type: "infographic" }]), [
    {
      type: "infographic",
      data: { type: "gauge", min_value: 0, max_value: 100, value: 0 },
      colors: [],
    },
  ]);
});

test("flex gets a required frame and distributes the main axis across children", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "flex",
        direction: "row",
        wrap: true,
        align_items: "center",
        justify_content: "space-between",
        gap: 10,
        padding: { top: 5, right: 5, bottom: 5, left: 5 },
        size: { width: 210, height: 110 },
        children: [
          { type: "text", text: "one" },
          { type: "text", text: "two", size: { width: 33, height: 44 } },
        ],
        max_children: 4,
        min_children: 1,
      },
    ]),
    [
      {
        size: { width: 210, height: 110 },
        position: { x: 0, y: 0 },
        layout: { grow: 1, shrink: 1 },
        type: "flex",
        direction: "row",
        wrap: true,
        align_items: "center",
        justify_content: null,
        padding: { top: 5, right: 5, bottom: 5, left: 5 },
        gap: 10,
        column_gap: null,
        row_gap: null,
        children: [
          {
            size: { width: 95, height: 100 },
            type: "text",
            font: null,
            alignment: null,
            fill: null,
            stroke: null,
            runs: [{ text: "one" }],
            max_length: null,
            min_length: null,
          },
          {
            size: { width: 33, height: 44 },
            type: "text",
            font: null,
            alignment: null,
            fill: null,
            stroke: null,
            runs: [{ text: "two" }],
            max_length: null,
            min_length: null,
          },
        ],
        max_children: 4,
        min_children: 1,
      },
    ],
  );
});

test("grid truncates columns/rows and sizes children into cells", () => {
  const [grid] = elementsOf([
    {
      type: "grid",
      columns: 2.9,
      rows: "2",
      gap: 10,
      align_items: "stretch",
      justify_items: "flex-start",
      size: { width: 210, height: 210 },
      children: [
        { type: "text", text: "a" },
        { type: "text", text: "b" },
        { type: "text", text: "c" },
      ],
    },
  ]);

  assert.ok(grid.type === "grid");
  assert.deepEqual(
    { columns: grid.columns, rows: grid.rows, padding: grid.padding },
    { columns: 2, rows: 2, padding: null },
  );
  assert.deepEqual(
    grid.children.map((child) => child.size),
    [
      { width: 100, height: 100 },
      { width: 100, height: 100 },
      { width: 100, height: 100 },
    ],
  );
});

test("withEqualTemplateV2FlowChildSizes only sizes uniform, measurable flows", () => {
  assert.deepEqual(
    withEqualTemplateV2FlowChildSizes({
      type: "flex",
      size: { width: 100, height: 100 },
      children: [{ type: "text" }, { type: "image" }],
    }),
    [{ type: "text" }, { type: "image" }],
  );
  assert.deepEqual(
    withEqualTemplateV2FlowChildSizes({
      type: "flex",
      children: [{ type: "text" }],
    }),
    [{ type: "text" }],
  );
  assert.deepEqual(
    withEqualTemplateV2FlowChildSizes({
      type: "flex",
      size: { width: 1, height: 1 },
    }),
    [],
  );
  assert.deepEqual(
    withEqualTemplateV2FlowChildSizes({
      type: "stack",
      size: { width: 10, height: 10 },
      children: [{ type: "text" }],
    }),
    [{ type: "text" }],
  );
  assert.deepEqual(
    withEqualTemplateV2FlowChildSizes({
      type: "grid-view",
      size: { width: 100, height: 100 },
      columns: 5,
      children: [{ type: "text" }, { type: "text" }],
    }),
    [
      { type: "text", size: { width: 50, height: 100 } },
      { type: "text", size: { width: 50, height: 100 } },
    ],
  );
});

test("group derives its frame from children and skips unknown child types", () => {
  assert.deepEqual(
    elementsOf([
      {
        type: "group",
        children: [
          {
            type: "text",
            position: { x: 10, y: 20 },
            size: { width: 30, height: 40 },
          },
          { type: "unknown" },
        ],
      },
    ]),
    [
      {
        position: { x: 0, y: 0 },
        size: { width: 40, height: 60 },
        type: "group",
        children: [
          {
            position: { x: 10, y: 20 },
            size: { width: 30, height: 40 },
            type: "text",
            font: null,
            alignment: null,
            fill: null,
            stroke: null,
            runs: [{ text: "Text" }],
            max_length: null,
            min_length: null,
          },
        ],
        max_children: null,
        min_children: null,
      },
    ],
  );
  assert.deepEqual(elementsOf([{ type: "group", children: [] }]), [
    {
      position: { x: 0, y: 0 },
      size: { width: 1, height: 1 },
      type: "group",
      children: [],
      max_children: null,
      min_children: null,
    },
  ]);
});

test("unrenderable layouts collapse to the invisible fallback element", () => {
  assert.deepEqual(elementsOf([{ type: "nope" }, {}, null, "x"]), [
    INVISIBLE_FALLBACK,
  ]);
  assert.deepEqual(adaptTemplateV2LayoutToSlide({}, 4), {
    title: "Slide 5",
    background: "FFFFFF",
    elements: [INVISIBLE_FALLBACK],
  });
});
