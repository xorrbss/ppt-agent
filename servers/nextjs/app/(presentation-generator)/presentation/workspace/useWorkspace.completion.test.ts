import assert from "node:assert/strict";
import test from "node:test";

import {
  fixedWorkspaceSlideIndexes,
  workspacePresentationChangeFor,
} from "./workspaceCompletion.ts";
import type { WorkspaceOperationAnchor } from "./types.ts";

function operation(
  overrides: Partial<WorkspaceOperationAnchor> = {},
): WorkspaceOperationAnchor {
  return {
    anchorType: "operation",
    id: "anchor-quality",
    operationId: "quality-1",
    position: 1,
    operationType: "quality.review",
    title: "고품질 검수",
    status: "succeeded",
    resultRevision: 12,
    result: {
      fixed_count: 2,
      fixed_slide_indices: [7, 2, 7],
    },
    events: [],
    allowedActions: [],
    ...overrides,
  };
}

test("quality refresh is emitted only after a committed revision and real fixes", () => {
  assert.deepEqual(workspacePresentationChangeFor(operation()), {
    operationId: "quality-1",
    operationType: "quality.review",
    resultRevision: 12,
    result: { fixed_count: 2, fixed_slide_indices: [7, 2, 7] },
    fixedSlideIndexes: [2, 7],
  });

  assert.equal(
    workspacePresentationChangeFor(operation({ resultRevision: undefined })),
    undefined,
  );
  assert.equal(
    workspacePresentationChangeFor(operation({ result: { fixed_count: 0 } })),
    undefined,
  );
});

test("quality slide indexes are safe, unique, and zero-based", () => {
  assert.deepEqual(
    fixedWorkspaceSlideIndexes({
      fixedSlideIndices: [3, -1, 3, 1.5, "2", 0],
    }),
    [0, 3],
  );
  assert.deepEqual(
    fixedWorkspaceSlideIndexes({ fixed_pages: [4, 1, 4] }),
    [0, 3],
  );
});
