import assert from "node:assert/strict";
import test from "node:test";

import { STATUS_LABELS, TERMINAL } from "./WorkspaceOperationLabels.ts";

test("every state the runner can end an operation in is terminal and labeled", () => {
  // These are the operation states the backend runner writes; a state missing
  // here renders as "unsupported", keeps its cancel button, and keeps polling.
  for (const state of ["succeeded", "failed", "cancelled", "dead_letter", "deadline_exceeded"]) {
    assert.ok(TERMINAL.has(state), `${state} must be terminal`);
    assert.ok(STATUS_LABELS[state], `${state} must have a label`);
  }
});

test("active states are not terminal", () => {
  for (const state of ["queued", "running", "awaiting_approval", "cancelling", "blocked"]) {
    assert.ok(!TERMINAL.has(state), `${state} must stay active`);
  }
});
