import assert from "node:assert/strict";
import test from "node:test";

import { buildTemplateV2ImportReview } from "./template-v2-import-review.ts";

test("import review maps candidates to editable and manual outcomes", () => {
  const review = buildTemplateV2ImportReview({
    candidates: {
      slides: [
        {
          shapes: [
            {
              source_id: "shape-1",
              name: "Title",
              kind: "text",
              x: 10,
              y: 20,
              width: 300,
              height: 40,
              confidence: 0.92,
            },
            {
              source_id: "shape-2",
              name: "Chart",
              kind: "unsupported",
              x: 50,
              y: 80,
              width: 200,
              height: 120,
              confidence: 0,
              unsupported_reason: "unsupported_graphic_frame",
            },
          ],
        },
      ],
    },
  });

  assert.deepEqual(review, {
    rows: [
      {
        slide: 1,
        sourceId: "shape-1",
        name: "Title",
        kind: "text",
        confidence: 0.92,
        geometry: "10, 20 · 300 × 40 px",
        outcome: "editable-text",
        reason: null,
      },
      {
        slide: 1,
        sourceId: "shape-2",
        name: "Chart",
        kind: "unsupported",
        confidence: 0,
        geometry: "50, 80 · 200 × 120 px",
        outcome: "manual-review",
        reason: "unsupported_graphic_frame",
      },
    ],
    total: 2,
    truncated: false,
  });
});

test("import review is bounded and rejects absent candidate contracts", () => {
  assert.equal(buildTemplateV2ImportReview({ summary: {} }), null);
  assert.deepEqual(
    buildTemplateV2ImportReview(
      {
        candidates: {
          slides: [{ shapes: [{ kind: "container" }, { kind: "text" }] }],
        },
      },
      1
    ),
    {
      rows: [
        {
          slide: 1,
          sourceId: "shape-1",
          name: "Unnamed shape",
          kind: "container",
          confidence: 0,
          geometry: "unknown",
          outcome: "editable-container",
          reason: null,
        },
      ],
      total: 2,
      truncated: true,
    }
  );
});
