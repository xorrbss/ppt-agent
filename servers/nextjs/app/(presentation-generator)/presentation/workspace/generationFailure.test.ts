import assert from "node:assert/strict";
import test from "node:test";

import {
  GENERATION_FAILURE_CLASSES,
  canResumeGenerationFailure,
  findResumedGeneration,
  generationFailureKey,
  isGenerationFailureStatus,
  isResumePrimary,
  parseGenerationFailureClass,
} from "./generationFailure.ts";

test("accepts only the contract classes", () => {
  for (const value of GENERATION_FAILURE_CLASSES) {
    assert.equal(parseGenerationFailureClass(value), value);
    assert.equal(generationFailureKey(value), value);
  }
  for (const value of ["", "Provider_Unstable", "workspace_request_failed", null, undefined, 3]) {
    assert.equal(parseGenerationFailureClass(value), null);
    assert.equal(generationFailureKey(value), null);
  }
});

test("resume is primary except for internal defects, unusable input and reached limits", () => {
  assert.equal(isResumePrimary("provider_unstable"), true);
  assert.equal(isResumePrimary("service_interrupted"), true);
  assert.equal(isResumePrimary("quality_not_passed"), true);
  assert.equal(isResumePrimary("unknown"), true);
  assert.equal(isResumePrimary(null), true);
  assert.equal(isResumePrimary("source_unprocessable"), false);
  assert.equal(isResumePrimary("limit_reached"), false);
  assert.equal(isResumePrimary("internal_defect"), false);
});

test("internal defects cannot offer recovery, while other classes retain it", () => {
  assert.equal(canResumeGenerationFailure("internal_defect"), false);
  for (const value of [...GENERATION_FAILURE_CLASSES.filter((value) => value !== "internal_defect"), null]) {
    assert.equal(canResumeGenerationFailure(value), true);
  }
});

test("terminal failure statuses", () => {
  for (const status of ["failed", "dead_letter", "deadline_exceeded", "expired"]) {
    assert.equal(isGenerationFailureStatus(status), true, status);
  }
  for (const status of ["cancelled", "rejected", "running", null, undefined]) {
    assert.equal(isGenerationFailureStatus(status), false, String(status));
  }
});

test("finds the deck a resume created by retry_of_operation_id", () => {
  const items = [
    { operationId: "a", presentationId: "p-a", retryOfOperationId: null },
    { operationId: "b", presentationId: "p-b", retryOfOperationId: "failed-1" },
  ];
  assert.equal(findResumedGeneration(items, "failed-1")?.presentationId, "p-b");
  assert.equal(findResumedGeneration(items, "other"), null);
});
