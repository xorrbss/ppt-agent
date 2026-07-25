import assert from "node:assert/strict";
import test from "node:test";

import { createTemplateV2SlideRenderPlan } from "./template-v2-render-plan.mjs";
import { renderTemplateV2GeneralSlideCanvasHtml } from "./template-v2-general-renderer.mjs";

/**
 * Both cases below are what the runtime PPTX analyzer actually persists, and both
 * were lost silently: a null `curve` failed the whole deck's render plan, and a
 * quoted font family truncated the style attribute so size/colour/weight vanished.
 */

function vectorSlide(curve: unknown) {
  return {
    ui: {
      components: [
        {
          id: "component",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "vector",
              shape: "polygon",
              closed: true,
              curve,
              points: [
                { x: 0, y: 0 },
                { x: 10, y: 0 },
                { x: 10, y: 10 },
                { x: 0, y: 10 },
              ],
              position: { x: 0, y: 0 },
              size: { width: 10, height: 10 },
              fill: { color: "#2E86C1" },
            },
          ],
        },
      ],
    },
  };
}

test("a null vector curve means absent, not invalid", () => {
  // pydantic's model_dump(mode="json") emits null for unset optionals rather than
  // omitting the key; rejecting it killed every deck holding one preset shape.
  assert.doesNotThrow(() => createTemplateV2SlideRenderPlan(vectorSlide(null)));
  assert.doesNotThrow(() => createTemplateV2SlideRenderPlan(vectorSlide(undefined)));
});

test("a malformed vector curve is still rejected", () => {
  assert.throws(
    () => createTemplateV2SlideRenderPlan(vectorSlide({ type: "jagged" })),
    /invalid_vector_curve/
  );
});

function slideWith(element: Record<string, unknown>) {
  return {
    ui: {
      components: [
        { id: "component", position: { x: 0, y: 0 }, elements: [element] },
      ],
    },
  };
}

function planOnlyElement(element: Record<string, unknown>) {
  return createTemplateV2SlideRenderPlan(slideWith(element)).components[0].elements[0];
}

test("a null vector shape means absent, not an invalid shape", () => {
  // The bundled converter emits straight-line connectors with no `shape` key at all;
  // Vector.shape is Optional, so model_dump(mode="json") writes it back as null.
  const node = planOnlyElement({
    type: "vector",
    closed: false,
    stroke: { color: "#333333", width: 1 },
    shape: null,
    points: [
      { x: 0, y: 0 },
      { x: 120, y: 80 },
    ],
  });

  assert.equal(node.vector.shape, null);
  assert.equal(node.vector.closed, false);
  assert.deepEqual(node.frame, { x: 0, y: 0, width: 120, height: 80 });
});

test("a malformed vector shape is still rejected", () => {
  assert.throws(
    () =>
      planOnlyElement({
        type: "vector",
        shape: "hexagon",
        points: [
          { x: 0, y: 0 },
          { x: 10, y: 10 },
        ],
      }),
    /invalid_vector_shape/
  );
});

test("a null grid rows means absent, not an invalid row count", () => {
  const node = planOnlyElement({
    type: "grid",
    columns: 2,
    rows: null,
    position: { x: 0, y: 0 },
    size: { width: 200, height: 100 },
    children: [
      {
        type: "vector",
        shape: "polygon",
        points: [
          { x: 0, y: 0 },
          { x: 40, y: 0 },
          { x: 40, y: 20 },
        ],
      },
    ],
  });

  // rows fell back to the derived count instead of tripping grid_contract_required.
  assert.equal(node.children.length, 1);
  assert.equal(node.frame.height, 100);
});

test("a malformed grid rows is still rejected", () => {
  assert.throws(
    () => planOnlyElement({ type: "grid", columns: 2, rows: 0, children: [] }),
    /grid_contract_required/
  );
});

test("a null chart size means absent, not an undersized chart", () => {
  const node = planOnlyElement({
    type: "chart",
    chart_type: "bar",
    size: null,
    categories: ["a", "b"],
    series: [{ name: "s", values: [1, 2] }],
  });

  assert.equal(node.frame.width, null);
  assert.equal(node.frame.height, null);
  assert.equal(node.chart.type, "bar");
  assert.deepEqual(node.chart.categories, ["a", "b"]);
});

test("a genuinely undersized chart is still rejected", () => {
  assert.throws(
    () =>
      planOnlyElement({
        type: "chart",
        chart_type: "bar",
        size: { width: 40, height: 30 },
        categories: ["a"],
        series: [{ name: "s", values: [1] }],
      }),
    /invalid_chart_size/
  );
});

test("run styling survives the style attribute instead of truncating it", () => {
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    ui: {
      components: [
        {
          id: "component",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "text",
              position: { x: 0, y: 0 },
              size: { width: 100, height: 20 },
              decorative: true,
              name: "t",
              min_length: 0,
              max_length: 100,
              runs: [
                {
                  text: "BOLD",
                  font: {
                    family: "Calibri",
                    size: 42.67,
                    color: "#C02030",
                    bold: true,
                  },
                },
              ],
            },
          ],
        },
      ],
    },
  });

  // Assert on what a parser sees, not on the raw string: an unescaped quote leaves
  // the later declarations physically present in the markup while the attribute
  // itself ends at that quote, so a substring check here would pass either way.
  const start = html.indexOf('style="', html.indexOf("<span"));
  assert.notEqual(start, -1, "the run must carry a style attribute");
  const value = html.slice(start + 7, html.indexOf('"', start + 7));

  assert.ok(value.includes("Calibri"), "font family must be inside the attribute");
  for (const declaration of ["font-size:42.67px", "color:#C02030", "font-weight:700"]) {
    assert.ok(value.includes(declaration), `${declaration} must be inside the attribute`);
  }
});

/**
 * Colours the renderer reads straight off the element -- fill, table-cell, run font --
 * never reach the render plan, so the plan's colour validator never saw them. They land
 * bare in a double-quoted style attribute that feeds dangerouslySetInnerHTML on the
 * export page. Same attribute-value parsing as above: with a breakout the discarded
 * declarations are still physically present in the blob, only the attribute ends early.
 */

function styleAttributeAfter(html: string, marker: string) {
  const anchor = html.indexOf(marker);
  assert.notEqual(anchor, -1, `expected ${marker} in the rendered markup`);
  const start = html.indexOf('style="', anchor);
  assert.notEqual(start, -1, "the element must carry a style attribute");
  return html.slice(start + 7, html.indexOf('"', start + 7));
}

test("a hostile fill colour cannot close the style attribute", () => {
  const hostile = { color: '#fff" onmouseover="alert(1)' };
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    ui: {
      components: [
        {
          id: "component",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "text",
              position: { x: 0, y: 0 },
              size: { width: 100, height: 20 },
              fill: hostile,
              runs: [{ text: "hi" }],
            },
            {
              type: "container",
              position: { x: 0, y: 40 },
              size: { width: 100, height: 20 },
              fill: hostile,
            },
          ],
        },
      ],
    },
  });

  const text = styleAttributeAfter(html, 'data-template-v2-element="text"');
  assert.ok(text.includes("background:transparent"), "fill must degrade to the fallback");
  // Declarations emitted after `background` prove the attribute did not end at the
  // injected quote; they were physically present but orphaned before the fix.
  assert.ok(text.includes("text-align:left"), "later declarations must stay in the attribute");
  assert.ok(text.includes("overflow:hidden"), "the attribute must reach its last declaration");

  const container = styleAttributeAfter(html, 'data-template-v2-element="container"');
  assert.ok(container.includes("background:transparent"), "container fill must degrade too");
  assert.ok(container.includes("overflow:hidden"), "container attribute must not be truncated");

  assert.ok(!html.includes("onmouseover"), "no event handler may reach the markup");
});

test("a hostile table cell colour cannot inject an element", () => {
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    ui: {
      components: [
        {
          id: "component",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "table",
              position: { x: 0, y: 0 },
              size: { width: 200, height: 60 },
              columns: [{ runs: [{ text: "head" }] }],
              rows: [
                [
                  {
                    color: { color: '#fff"><img src=x onerror=alert(1)>' },
                    runs: [{ text: "body" }],
                  },
                ],
              ],
            },
          ],
        },
      ],
    },
  });

  const cell = styleAttributeAfter(html, "<td");
  assert.ok(cell.includes("background:transparent"), "cell colour must degrade to the fallback");
  assert.ok(cell.includes("border:1px solid #d1d5db"), "the cell attribute must not be truncated");
  assert.ok(!html.includes("<img"), "no injected element may reach the markup");
  assert.ok(!html.includes("onerror"), "no event handler may reach the markup");
});

test("a font colour cannot smuggle extra declarations past escaping", () => {
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    ui: {
      components: [
        {
          id: "component",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "text",
              position: { x: 0, y: 0 },
              size: { width: 100, height: 20 },
              runs: [
                {
                  text: "hi",
                  // Escaping leaves `;` `:` `(` `)` untouched, so this survived it whole.
                  font: { size: 12, bold: true, color: "red;background:url(http://x/beacon)" },
                },
              ],
            },
          ],
        },
      ],
    },
  });

  const run = styleAttributeAfter(html, "<span");
  assert.ok(run.includes("font-weight:700"), "the run style must actually be rendered");
  assert.ok(!run.includes("background"), "no smuggled declaration may enter the attribute");
  assert.ok(!run.includes("url("), "no smuggled url may enter the attribute");
  assert.ok(!html.includes("beacon"), "the payload must not survive anywhere in the markup");
});

test("an infographic colour cannot escape the gradient it is interpolated into", () => {
  // `plan.infographic.colors` is validated for type but not value, so it is the same
  // sink as `fill.color` with an extra way out: `)` leaves `conic-gradient(` as well.
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    ui: {
      components: [
        {
          id: "component",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "infographic",
              position: { x: 0, y: 0 },
              size: { width: 100, height: 100 },
              colors: [
                "#2563eb 0 50%,#000 0 100%);background:url(http://x/beacon",
                "#eee;background:url(http://x/beacon)",
              ],
              data: { type: "gauge", value: 50, min_value: 0, max_value: 100 },
            },
          ],
        },
      ],
    },
  });

  const styles = [...html.matchAll(/style="([^"]*)"/g)].map((match) => match[1]);
  const gradient = styles.find((style) => style.includes("conic-gradient"));
  assert.ok(gradient, "the gauge graphic must actually be rendered");
  assert.ok(
    gradient.startsWith("position:relative") && gradient.endsWith("100%)"),
    "the declaration list must still be the one the renderer wrote"
  );
  for (const style of styles) {
    assert.ok(!style.includes("url("), "no smuggled url may enter any style attribute");
  }
  assert.ok(!html.includes("beacon"), "the payload must not survive anywhere in the markup");
});
