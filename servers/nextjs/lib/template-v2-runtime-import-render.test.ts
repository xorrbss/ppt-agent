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
