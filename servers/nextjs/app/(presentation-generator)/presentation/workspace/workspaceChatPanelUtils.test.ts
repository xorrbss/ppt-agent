import assert from "node:assert/strict";
import test from "node:test";

import { activeExportOperationType } from "./workspaceChatPanelUtils.ts";
import type {
  WorkspaceOperationAnchor,
  WorkspaceTimelineAnchor,
} from "./types.ts";

function operationAnchor(
  overrides: Partial<WorkspaceOperationAnchor> &
    Pick<WorkspaceOperationAnchor, "id" | "operationType" | "status">,
): WorkspaceOperationAnchor {
  return {
    anchorType: "operation",
    operationId: overrides.id,
    position: 0,
    title: overrides.operationType,
    events: [],
    allowedActions: [],
    ...overrides,
  };
}

test("returns the operation type of a running export.pptx operation", () => {
  const items: WorkspaceTimelineAnchor[] = [
    operationAnchor({ id: "op-1", operationType: "export.pptx", status: "running" }),
  ];
  assert.equal(activeExportOperationType(items), "export.pptx");
});

test("returns the operation type of a queued export.pdf operation", () => {
  const items: WorkspaceTimelineAnchor[] = [
    operationAnchor({ id: "op-1", operationType: "export.pdf", status: "queued" }),
  ];
  assert.equal(activeExportOperationType(items), "export.pdf");
});

test("returns null once the export operation reaches a terminal state", () => {
  for (const status of ["succeeded", "failed", "cancelled", "dead_letter", "deadline_exceeded"]) {
    const items: WorkspaceTimelineAnchor[] = [
      operationAnchor({ id: "op-1", operationType: "export.pptx", status }),
    ];
    assert.equal(activeExportOperationType(items), null, `status ${status} must not be in flight`);
  }
});

test("ignores non-export operations and non-operation anchors", () => {
  const items: WorkspaceTimelineAnchor[] = [
    {
      anchorType: "message",
      id: "msg-1",
      position: 0,
      role: "user",
      content: "hi",
      attachments: [],
    },
    operationAnchor({ id: "op-1", operationType: "slide.edit", status: "running" }),
  ];
  assert.equal(activeExportOperationType(items), null);
});

test("finds an in-flight export even when it is not the first item", () => {
  const items: WorkspaceTimelineAnchor[] = [
    operationAnchor({ id: "op-1", operationType: "export.pdf", status: "succeeded" }),
    operationAnchor({ id: "op-2", operationType: "export.pptx", status: "running" }),
  ];
  assert.equal(activeExportOperationType(items), "export.pptx");
});
