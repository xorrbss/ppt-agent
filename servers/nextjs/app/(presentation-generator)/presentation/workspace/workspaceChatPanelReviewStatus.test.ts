import assert from "node:assert/strict";
import test from "node:test";

import { finalPptxReviewStatusFor } from "./workspaceChatPanelReviewStatus.ts";
import { normalizeTimelinePage } from "./timelineProjection.ts";

const artifactId = "00000000-0000-0000-0000-000000000123";

function normalizedOperation(overrides: Record<string, unknown> = {}) {
  const page = normalizeTimelinePage({
    schema_version: 1,
    items: [
      {
        anchor_type: "operation",
        id: "review-operation",
        operation_id: "review-operation",
        position: 1,
        operation_type: "quality.review",
        title: "Final PPTX review",
        status: "queued",
        capacity_waiting: true,
        wait_reason: "svg_final_pptx_office_instance_busy",
        target: {
          presentation_id: "00000000-0000-0000-0000-000000000456",
          summary: "quality review",
          context: {
            artifact_id: artifactId,
            review_target: "final_pptx",
          },
        },
        ...overrides,
      },
    ],
    has_more: false,
  });
  return page.items.filter((item) => item.anchorType === "operation");
}

test("matches deferred final PPTX review from normalized target context", () => {
  assert.equal(
    finalPptxReviewStatusFor(normalizedOperation(), artifactId),
    "deferred",
  );
});

test("matches running final PPTX review from normalized target context", () => {
  assert.equal(
    finalPptxReviewStatusFor(
      normalizedOperation({
        status: "running",
        capacity_waiting: false,
        wait_reason: null,
      }),
      artifactId,
    ),
    "running",
  );
});
