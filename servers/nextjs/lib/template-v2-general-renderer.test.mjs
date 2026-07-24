import assert from "node:assert/strict";
import test from "node:test";

import {
  TEMPLATE_V2_RENDERED_ELEMENT_TYPES,
  renderTemplateV2GeneralPresentationHtml,
  renderTemplateV2GeneralSlideCanvasHtml,
} from "./template-v2-general-renderer.mjs";

const run = (text) => ({ text });
const frame = { position: { x: 10, y: 20 }, size: { width: 240, height: 120 } };

function slideWith(elements) {
  return {
    id: "slide-one",
    ui: {
      components: [{ id: "body", position: { x: 0, y: 0 }, elements }],
    },
  };
}

test("renderer covers every strict Template V2 element discriminator", () => {
  assert.deepEqual(TEMPLATE_V2_RENDERED_ELEMENT_TYPES, [
    "text",
    "container",
    "image",
    "text-list",
    "table",
    "vector",
    "chart",
    "infographic",
    "flex",
    "grid",
    "group",
  ]);

  const elements = [
    { type: "text", ...frame, runs: [run("plain")], name: "text" },
    { type: "container", ...frame, child: { type: "text", ...frame, runs: [run("nested")], name: "nested" } },
    { type: "image", ...frame, data: "data:image/png;base64,AA==", name: "image" },
    { type: "text-list", ...frame, marker: "number", items: [[run("one")], [run("two")]], name: "list" },
    {
      type: "table",
      ...frame,
      columns: [{ runs: [run("heading")] }],
      rows: [[{ runs: [run("value")] }]],
      name: "table",
    },
    {
      type: "vector",
      points: [{ x: 20, y: 20 }, { x: 120, y: 30 }, { x: 80, y: 100 }],
      closed: true,
      curve: { type: "smooth", tension: 0.5, segments: 4 },
      fill: { color: "#2563eb" },
      stroke: { color: "#111827", width: 2 },
    },
    {
      type: "chart",
      ...frame,
      chart_type: "bar",
      title: "Revenue",
      categories: ["A", "B"],
      series: [{ name: "FY26", values: [10, 20] }],
      legend: true,
      name: "chart",
    },
    {
      type: "infographic",
      ...frame,
      data: { type: "progress_bar", min_value: 0, max_value: 100, value: 72 },
      colors: ["#2563eb", "#e5e7eb"],
      name: "progress",
    },
    {
      type: "flex",
      ...frame,
      direction: "row",
      gap: 8,
      children: [{ type: "text", size: { width: 80, height: 40 }, runs: [run("flex child")], name: "flex child" }],
      name: "flex",
    },
    {
      type: "grid",
      ...frame,
      columns: 2,
      gap: 8,
      children: [{ type: "text", size: { width: 80, height: 40 }, runs: [run("grid child")], name: "grid child" }],
      name: "grid",
    },
    {
      type: "group",
      ...frame,
      children: [{ type: "text", position: { x: 0, y: 0 }, size: { width: 80, height: 40 }, runs: [run("group child")], name: "group child" }],
      name: "group",
    },
  ];

  const html = renderTemplateV2GeneralSlideCanvasHtml(slideWith(elements));
  for (const type of TEMPLATE_V2_RENDERED_ELEMENT_TYPES) {
    assert.match(html, new RegExp(`data-template-v2-element="${type}"`));
  }
  assert.match(html, /data-chart-type="bar"/);
  assert.match(html, /data-infographic-type="progress_bar"/);
  assert.match(html, /position:relative/);
});

test("renderer escapes authored values and still fails closed for unknown types", () => {
  const html = renderTemplateV2GeneralPresentationHtml({
    version: "v2-standard",
    mode: "template",
    slides: [slideWith([{ type: "text", ...frame, runs: [run("<script>alert(1)</script>")], name: '" unsafe' }])],
  });
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(html, /&quot; unsafe/);
  assert.match(html, /data-template-v2-text-runs/);
  assert.doesNotMatch(html, /flex-direction:column/);

  assert.throws(
    () => renderTemplateV2GeneralSlideCanvasHtml(slideWith([{ type: "future-element" }])),
    /template_v2_renderer_unsupported_element:future-element/
  );
});

test("renderer validates nested rich-data contracts", () => {
  assert.throws(
    () => renderTemplateV2GeneralSlideCanvasHtml(slideWith([{ type: "text-list", ...frame, items: [null] }])),
    /template_v2_renderer_text_runs_required/
  );
  assert.throws(
    () => renderTemplateV2GeneralSlideCanvasHtml(slideWith([{ type: "grid", ...frame, columns: 0, children: [] }])),
    /template_v2_renderer_grid_contract_required/
  );
  assert.throws(
    () => renderTemplateV2GeneralSlideCanvasHtml(slideWith([{ type: "chart", ...frame, chart_type: "bar", series: [{}] }])),
    /template_v2_renderer_invalid_chart_series/
  );
});

test("renderer preserves advanced appearance and image semantics", () => {
  const html = renderTemplateV2GeneralSlideCanvasHtml(
    slideWith([
      {
        type: "text",
        ...frame,
        runs: [run("outlined")],
        stroke: { color: "#123456", width: 2, opacity: 0.5 },
        shadow: { color: "#000000", blur: 4, opacity: 0.25, offset_x: 1, offset_y: 2 },
      },
      {
        type: "container",
        ...frame,
        alignment: { horizontal: "right", vertical: "bottom" },
        shadow: { color: "#111111", blur: 5, opacity: 0.5 },
        child: { type: "text", runs: [run("child")] },
      },
      {
        type: "image",
        ...frame,
        data: "data:image/png;base64,AA==",
        fit: "cover",
        flip_h: true,
        flip_v: true,
        opacity: 0.75,
        focus_x: 25,
        focus_y: 80,
        crop_scale: 2,
        clip_path: "circle(45% at 50% 50%)",
      },
      {
        type: "image",
        ...frame,
        data: "icon.svg",
        is_icon: true,
        color: "#abcdef",
      },
    ])
  );

  assert.match(html, /-webkit-text-stroke:2px rgba\(18,52,86,0.5\)/);
  assert.match(html, /text-shadow:1px 2px 4px rgba\(0,0,0,0.25\)/);
  assert.match(html, /align-items:flex-end;justify-content:flex-end/);
  assert.match(html, /box-shadow:0px 0px 5px rgba\(17,17,17,0.5\)/);
  assert.match(html, /transform:scaleX\(-1\) scaleY\(-1\)/);
  assert.match(html, /object-position:25% 80%/);
  assert.match(html, /transform:scale\(2\)/);
  assert.match(html, /clip-path:circle\(45% at 50% 50%\)/);
  assert.match(html, /data-template-v2-image-mask/);
  assert.match(html, /background:#abcdef/);
});

test("stacked charts accumulate series and render all supported chart annotations", () => {
  const html = renderTemplateV2GeneralSlideCanvasHtml(
    slideWith([
      {
        type: "chart",
        ...frame,
        chart_type: "stacked_bar",
        categories: ["A", "B"],
        series: [
          { name: "One", values: [2, -1] },
          { name: "Two", values: [3, -4] },
        ],
        title: "Totals",
        legend: true,
        x_axis: true,
        y_axis: true,
        x_axis_title: "Category",
        y_axis_title: "Value",
        x_axis_grid: true,
        y_axis_grid: true,
        data_labels: "mid",
        source: "Internal",
      },
    ])
  );

  assert.match(html, /data-chart-stacked="true"/);
  assert.match(html, /data-stack-start="2" data-stack-end="5"/);
  assert.match(html, /data-stack-start="-1" data-stack-end="-5"/);
  assert.match(html, /data-chart-axis="x"/);
  assert.match(html, /data-chart-axis="y"/);
  assert.match(html, /data-chart-grid="x"/);
  assert.match(html, /data-chart-grid="y"/);
  assert.match(html, /data-chart-axis-title="x"/);
  assert.match(html, /data-chart-axis-title="y"/);
  assert.match(html, /data-chart-data-label="mid"/);
  assert.match(html, /data-chart-editable-text-layer/);
  assert.match(html, /data-chart-category="0"[^>]*>A</);
  assert.match(html, /data-chart-axis-title="x"[^>]*>Category</);
  assert.match(html, /data-chart-legend/);
  assert.match(html, /data-chart-source/);
  assert.match(html, />Internal</);
});
