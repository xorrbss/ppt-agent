import assert from "node:assert/strict";
import test from "node:test";

import {
  assertTemplateV2PlanClosedVector,
  createTemplateV2SlideRenderPlan,
  sampleTemplateV2SmoothPoints,
} from "./template-v2-render-plan.mjs";
import { renderTemplateV2GeneralSlideCanvasHtml } from "./template-v2-general-renderer.mjs";

function slide(elements, componentPosition = { x: 0, y: 0 }) {
  return {
    ui: {
      components: [
        {
          id: "component",
          position: componentPosition,
          elements,
        },
      ],
    },
  };
}

function frame(node) {
  return {
    x: node.frame.x,
    y: node.frame.y,
    width: node.frame.width,
    height: node.frame.height,
  };
}

test("flex plans deterministic local and slide-absolute child frames", () => {
  const plan = createTemplateV2SlideRenderPlan(
    slide(
      [
        {
          type: "flex",
          position: { x: 100, y: 50 },
          size: { width: 300, height: 100 },
          direction: "row",
          gap: 10,
          align_items: "center",
          justify_content: "center",
          children: [
            { type: "text", size: { width: 50, height: 20 }, runs: [] },
            { type: "text", size: { width: 70, height: 40 }, runs: [] },
          ],
        },
      ],
      { x: 10, y: 20 }
    )
  );
  const flex = plan.components[0].elements[0];

  assert.deepEqual(frame(flex.children[0]), { x: 85, y: 40, width: 50, height: 20 });
  assert.deepEqual(flex.children[0].absoluteFrame, {
    x: 195,
    y: 110,
    width: 50,
    height: 20,
  });
  assert.deepEqual(frame(flex.children[1]), { x: 145, y: 30, width: 70, height: 40 });
  assert.deepEqual(flex.children[1].absoluteFrame, {
    x: 255,
    y: 100,
    width: 70,
    height: 40,
  });
});

test("wrapped flex produces stable line geometry and infers the cross size", () => {
  const plan = createTemplateV2SlideRenderPlan(
    slide([
      {
        type: "flex",
        position: { x: 4, y: 6 },
        size: { width: 130 },
        direction: "row",
        wrap: true,
        gap: 10,
        align_items: "flex-start",
        children: [0, 1, 2].map(() => ({
          type: "text",
          size: { width: 60, height: 20 },
          runs: [],
        })),
      },
    ])
  );
  const flex = plan.components[0].elements[0];

  assert.deepEqual(frame(flex), { x: 4, y: 6, width: 130, height: 50 });
  assert.deepEqual(flex.children.map(frame), [
    { x: 0, y: 0, width: 60, height: 20 },
    { x: 70, y: 0, width: 60, height: 20 },
    { x: 0, y: 30, width: 60, height: 20 },
  ]);
});

test("grid plans equal cells, alignment, stretching, and absolute offsets", () => {
  const plan = createTemplateV2SlideRenderPlan(
    slide(
      [
        {
          type: "grid",
          position: { x: 20, y: 30 },
          size: { width: 220, height: 120 },
          columns: 2,
          rows: 2,
          column_gap: 20,
          row_gap: 10,
          align_items: "center",
          justify_items: "center",
          children: [
            { type: "text", size: { width: 40, height: 20 }, runs: [] },
            { type: "text", runs: [] },
            { type: "text", size: { width: 100, height: 55 }, runs: [] },
          ],
        },
      ],
      { x: 5, y: 7 }
    )
  );
  const grid = plan.components[0].elements[0];

  assert.deepEqual(grid.children.map(frame), [
    { x: 30, y: 17.5, width: 40, height: 20 },
    { x: 170, y: 27.5, width: 0, height: 0 },
    { x: 0, y: 65, width: 100, height: 55 },
  ]);
  assert.deepEqual(grid.children[0].absoluteFrame, {
    x: 55,
    y: 54.5,
    width: 40,
    height: 20,
  });
});

test("grid stretch fills omitted child dimensions", () => {
  const plan = createTemplateV2SlideRenderPlan(
    slide([
      {
        type: "grid",
        size: { width: 210, height: 90 },
        columns: 2,
        column_gap: 10,
        children: [{ type: "text", runs: [] }, { type: "text", runs: [] }],
      },
    ])
  );
  const grid = plan.components[0].elements[0];

  assert.deepEqual(grid.children.map(frame), [
    { x: 0, y: 0, width: 100, height: 90 },
    { x: 110, y: 0, width: 100, height: 90 },
  ]);
});

test("smooth vector sampling preserves open endpoints and explicitly closes polygons", () => {
  const points = [
    { x: 10, y: 20 },
    { x: 30, y: 10 },
    { x: 50, y: 40 },
  ];
  const open = sampleTemplateV2SmoothPoints(points, { segments: 4 });
  const closed = sampleTemplateV2SmoothPoints(points, { closed: true, segments: 4 });

  assert.equal(open.length, 9);
  assert.deepEqual(open[0], points[0]);
  assert.deepEqual(open.at(-1), points.at(-1));
  assert.equal(closed.length, 13);
  assert.deepEqual(closed.at(-1), closed[0]);

  const plan = createTemplateV2SlideRenderPlan(
    slide([
      {
        type: "vector",
        shape: "polygon",
        points,
        curve: { type: "smooth", tension: 0.5, segments: 4 },
      },
    ])
  );
  const vector = plan.components[0].elements[0].vector;
  assert.equal(vector.points.length, 13);
  assert.equal(assertTemplateV2PlanClosedVector(vector), true);
  assert.deepEqual(vector.points.at(-1), vector.points[0]);
});

test("chart plans preserve validated semantics", () => {
  const chart = createTemplateV2SlideRenderPlan(
    slide([
      {
        type: "chart",
        chart_type: "line",
        size: { width: 320, height: 180 },
        categories: ["Q1", "Q2"],
        series: [{ name: "Revenue", values: [12, 18] }],
        colors: ["#123456"],
      },
    ])
  ).components[0].elements[0].chart;

  assert.deepEqual(chart, {
    type: "line",
    categories: ["Q1", "Q2"],
    series: [{ name: "Revenue", values: [12, 18] }],
    colors: ["#123456"],
    title: null,
    titleColor: "#111827",
    legendColor: "#374151",
    axisColor: "#6b7280",
    gridColor: "#e5e7eb",
    xAxis: false,
    yAxis: false,
    xAxisTitle: null,
    yAxisTitle: null,
    xAxisGrid: false,
    yAxisGrid: false,
    dataLabels: null,
    legend: false,
    source: null,
    horizontal: false,
    stacked: false,
  });
});

test("appearance and chart semantics are normalized without silent field loss", () => {
  const nodes = createTemplateV2SlideRenderPlan(
    slide([
      {
        type: "text",
        runs: [],
        alignment: { horizontal: "right", vertical: "bottom" },
        stroke: { color: "#123456", width: 2, opacity: 0.5 },
        shadow: { color: "#000", blur: 4, opacity: 0.25, offset_x: 1, offset_y: 2 },
      },
      {
        type: "container",
        alignment: { horizontal: "center", vertical: "middle" },
        shadow: { color: "#111", blur: 5 },
      },
      {
        type: "image",
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
        type: "chart",
        chart_type: "horizontal_stacked_bar",
        categories: ["A"],
        series: [{ name: "One", values: [2] }, { name: "Two", values: [3] }],
        title: "Totals",
        legend: true,
        x_axis: true,
        y_axis: true,
        x_axis_title: "Value",
        y_axis_title: "Category",
        x_axis_grid: true,
        y_axis_grid: true,
        data_labels: "mid",
        source: "Internal",
      },
    ])
  ).components[0].elements;

  assert.deepEqual(nodes[0].text, {
    alignment: { horizontal: "right", vertical: "bottom" },
    stroke: { color: "#123456", width: 2, opacity: 0.5 },
    shadow: { color: "#000", blur: 4, opacity: 0.25, offsetX: 1, offsetY: 2 },
  });
  assert.deepEqual(nodes[1].container.alignment, {
    horizontal: "center",
    vertical: "middle",
  });
  assert.deepEqual(nodes[2].image, {
    fit: "cover",
    flipH: true,
    flipV: true,
    opacity: 0.75,
    focusX: 25,
    focusY: 80,
    cropScale: 2,
    clipPath: "circle(45% at 50% 50%)",
    color: null,
    isIcon: false,
  });
  assert.equal(nodes[3].chart.horizontal, true);
  assert.equal(nodes[3].chart.stacked, true);
  assert.equal(nodes[3].chart.dataLabels, "mid");
  assert.equal(nodes[3].chart.source, "Internal");
});

test("chart plans fail closed for unsupported or lossy contracts", () => {
  const invalidCharts = [
    {
      element: { type: "chart", chart_type: "treemap" },
      code: "template_v2_render_plan_unsupported_chart_type",
    },
    {
      element: {
        type: "chart",
        chart_type: "line",
        categories: ["Q1"],
        series: [{ name: "Revenue", values: [1, 2] }],
      },
      code: "template_v2_render_plan_chart_category_mismatch",
    },
    {
      element: {
        type: "chart",
        chart_type: "bar",
        series: [{ name: "Revenue", values: [1, "2"] }],
      },
      code: "template_v2_render_plan_invalid_chart_value",
    },
    {
      element: {
        type: "chart",
        chart_type: "pie",
        series: [
          { name: "One", values: [1] },
          { name: "Two", values: [2] },
        ],
      },
      code: "template_v2_render_plan_chart_series_limit",
    },
    {
      element: {
        type: "chart",
        chart_type: "bar",
        size: { width: 79, height: 60 },
      },
      code: "template_v2_render_plan_invalid_chart_size",
    },
    {
      element: { type: "chart", chart_type: "pie", x_axis: true },
      code: "template_v2_render_plan_unsupported_chart_axes",
    },
    {
      element: { type: "chart", chart_type: "donut", data_labels: "outside" },
      code: "template_v2_render_plan_unsupported_chart_data_labels",
    },
  ];

  for (const { element, code } of invalidCharts) {
    assert.throws(
      () => createTemplateV2SlideRenderPlan(slide([element])),
      new RegExp(`^Error: ${code}:`)
    );
  }
});

test("ambiguous appearance contracts fail closed", () => {
  const invalid = [
    {
      element: {
        type: "text",
        runs: [],
        stroke: { color: "#000", width: 1, dash: [2, 2] },
      },
      code: "template_v2_render_plan_unsupported_stroke_dash",
    },
    {
      element: {
        type: "image",
        data: "x",
        color: "#fff",
        is_icon: false,
      },
      code: "template_v2_render_plan_unsupported_image_color",
    },
    {
      element: {
        type: "image",
        data: "x",
        clip_path: "url(javascript:alert(1))",
      },
      code: "template_v2_render_plan_unsupported_image_clip_path",
    },
    {
      element: {
        type: "image",
        data: "x",
        crop_scale: 0.5,
      },
      code: "template_v2_render_plan_invalid_image_crop_scale",
    },
  ];
  for (const { element, code } of invalid) {
    assert.throws(
      () => createTemplateV2SlideRenderPlan(slide([element])),
      new RegExp(`^Error: ${code}:`)
    );
  }
});

test("infographic plans compute ratios and fail closed outside backend ranges", () => {
  const node = createTemplateV2SlideRenderPlan(
    slide([
      {
        type: "infographic",
        data: { type: "gauge", min_value: -20, max_value: 80, value: 30 },
        colors: ["#00f", "#ddd"],
      },
    ])
  ).components[0].elements[0];
  assert.deepEqual(node.infographic, {
    type: "gauge",
    minimum: -20,
    maximum: 80,
    value: 30,
    ratio: 0.5,
    colors: ["#00f", "#ddd"],
  });

  const invalid = [
    { data: { type: "dial", min_value: 0, max_value: 1, value: 0 } },
    { data: { type: "gauge", min_value: 1, max_value: 1, value: 1 } },
    { data: { type: "progress_bar", min_value: 0, max_value: 1, value: 2 } },
  ];
  for (const element of invalid) {
    assert.throws(
      () => createTemplateV2SlideRenderPlan(slide([{ type: "infographic", ...element }])),
      /^Error: template_v2_render_plan_/
    );
  }
});

test("unknown elements and over-capacity grids fail closed", () => {
  assert.throws(
    () => createTemplateV2SlideRenderPlan(slide([{ type: "video" }])),
    /^Error: template_v2_render_plan_unsupported_element:/
  );
  assert.throws(
    () =>
      createTemplateV2SlideRenderPlan(
        slide([
          {
            type: "grid",
            columns: 1,
            rows: 1,
            children: [{ type: "text", runs: [] }, { type: "text", runs: [] }],
          },
        ])
      ),
    /^Error: template_v2_render_plan_grid_capacity_exceeded:/
  );
});

test("general renderer consumes planned flex frames and sampled vector geometry", () => {
  const html = renderTemplateV2GeneralSlideCanvasHtml(
    slide([
      {
        type: "flex",
        position: { x: 100, y: 50 },
        size: { width: 300, height: 100 },
        direction: "row",
        gap: 10,
        align_items: "center",
        justify_content: "center",
        children: [
          { type: "text", size: { width: 50, height: 20 }, runs: [] },
          {
            type: "vector",
            shape: "polygon",
            points: [
              { x: 10, y: 20 },
              { x: 30, y: 10 },
              { x: 50, y: 40 },
            ],
            curve: { type: "smooth", tension: 0.5, segments: 4 },
          },
        ],
      },
    ])
  );

  assert.match(html, /data-template-v2-layout="absolute-plan"/);
  assert.match(html, /position:absolute;left:100px;top:40px;width:50px;height:20px/);
  const vectorPoints = html.match(/<polygon points="([^"]+)"/)?.[1].split(" ");
  assert.equal(vectorPoints?.length, 13);
});
