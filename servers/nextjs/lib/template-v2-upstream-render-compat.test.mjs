import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  renderTemplateV2GeneralSlideCanvasHtml,
} from "./template-v2-general-renderer.mjs";
import {
  createTemplateV2SlideRenderPlan,
} from "./template-v2-render-plan.mjs";

const golden = JSON.parse(
  readFileSync(
    new URL(
      "./fixtures/template-v2-upstream-render-compat-57b194b.json",
      import.meta.url
    ),
    "utf8"
  )
);
const upstream = JSON.parse(
  readFileSync(
    new URL(
      "../../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json",
      import.meta.url
    ),
    "utf8"
  )
);

const SEMANTIC_KEYS = [
  "text",
  "container",
  "image",
  "vector",
  "chart",
  "infographic",
];

function fixtureSlide(element) {
  return {
    id: `upstream-${element.type}`,
    ui: {
      components: [
        {
          id: "fixture",
          position: { x: 0, y: 0 },
          elements: [element],
        },
      ],
    },
  };
}

function frameTuple(frame) {
  return [frame.x, frame.y, frame.width, frame.height];
}

function planSemantics(element) {
  const plan = createTemplateV2SlideRenderPlan(fixtureSlide(element), {
    pathPrefix: `upstream.${element.type}`,
  });
  const node = plan.components[0].elements[0];
  const semanticKey =
    SEMANTIC_KEYS.find((candidate) => node[candidate] !== undefined) ?? null;
  return {
    frame: frameTuple(node.frame),
    semantic_key: semanticKey,
    semantic: semanticKey === null ? null : node[semanticKey],
  };
}

function score(results) {
  const supported = results.filter((result) => result === "supported").length;
  const total = results.length;
  return {
    supported,
    unsupported: total - supported,
    total,
    percent: Number(((supported / total) * 100).toFixed(2)),
  };
}

function applyDocumentedUpstreamCoercions(element) {
  const normalized = structuredClone(element);
  for (const coercion of golden.documented_coercions) {
    if (
      normalized.type === coercion.type &&
      normalized[coercion.field] === coercion.from
    ) {
      normalized[coercion.field] = coercion.to;
    }
  }
  return normalized;
}

test("golden fixture stays pinned to the reviewed upstream element contract", () => {
  assert.equal(upstream.upstream_sha, golden.upstream_sha);
  assert.equal(
    golden.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83"
  );
  assert.deepEqual(
    upstream.elements.map((element) => element.type),
    golden.cases.map((entry) => entry.type)
  );
  assert.equal(new Set(golden.cases.map((entry) => entry.type)).size, 11);
});

test("raw upstream elements match the golden render-plan and renderer boundary", () => {
  const results = [];

  for (const expected of golden.cases) {
    const element = upstream.elements.find(
      (candidate) => candidate.type === expected.type
    );
    assert.ok(element, `missing upstream fixture element: ${expected.type}`);

    if (expected.direct === "unsupported") {
      assert.throws(
        () => planSemantics(element),
        (error) => error?.message === expected.plan_error
      );
      assert.throws(
        () => renderTemplateV2GeneralSlideCanvasHtml(fixtureSlide(element)),
        (error) => error?.message === expected.renderer_error
      );
      results.push("unsupported");
      continue;
    }

    assert.deepEqual(planSemantics(element), expected.plan);
    const html = renderTemplateV2GeneralSlideCanvasHtml(fixtureSlide(element));
    for (const marker of expected.renderer_markers) {
      assert.ok(
        html.includes(marker),
        `${expected.type} renderer semantic marker missing: ${marker}`
      );
    }
    results.push("supported");
  }

  assert.deepEqual(score(results), golden.scores.direct_wire);
});

test("documented upstream coercion closes the chart compatibility gap", () => {
  const results = [];

  for (const expected of golden.cases) {
    const source = upstream.elements.find(
      (candidate) => candidate.type === expected.type
    );
    assert.ok(source, `missing upstream fixture element: ${expected.type}`);
    const element = applyDocumentedUpstreamCoercions(source);
    const expectedPlan = expected.normalized_plan ?? expected.plan;

    assert.deepEqual(planSemantics(element), expectedPlan);
    const html = renderTemplateV2GeneralSlideCanvasHtml(fixtureSlide(element));
    for (const marker of expected.renderer_markers) {
      assert.ok(
        html.includes(marker),
        `${expected.type} normalized renderer marker missing: ${marker}`
      );
    }
    results.push("supported");
  }

  assert.deepEqual(score(results), golden.scores.after_upstream_coercion);
});
