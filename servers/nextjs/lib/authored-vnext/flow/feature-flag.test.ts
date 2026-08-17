import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORED_VNEXT_FLOW_FEATURE_FLAG,
  AUTHORED_VNEXT_DEV_SLIDE_COUNT_FEATURE_FLAG,
  authoredVnextSlideCount,
  isAuthoredVnextFlowEnabled,
  selectPresentationFlow,
} from "./feature-flag.ts";
import { createAuthoredVnextFlowState } from "./reducer.ts";

test("authored vNext flow is default-off and only exact lowercase true enables it", () => {
  assert.equal(AUTHORED_VNEXT_FLOW_FEATURE_FLAG, "NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED");
  assert.equal(isAuthoredVnextFlowEnabled(undefined), false);
  assert.equal(isAuthoredVnextFlowEnabled("false"), false);
  assert.equal(isAuthoredVnextFlowEnabled("TRUE"), false);
  assert.equal(isAuthoredVnextFlowEnabled("1"), false);
  assert.equal(isAuthoredVnextFlowEnabled("true"), true);
});

test("three-slide harness is an exact development-only opt-in", () => {
  assert.equal(AUTHORED_VNEXT_DEV_SLIDE_COUNT_FEATURE_FLAG, "NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT");
  assert.equal(authoredVnextSlideCount(undefined, "development"), 90);
  assert.equal(authoredVnextSlideCount("3", "development"), 3);
  assert.equal(authoredVnextSlideCount("3", "production"), 90);
  assert.equal(authoredVnextSlideCount("4", "development"), 90);
});

test("disabled selection preserves the legacy contract by reference and never constructs vNext", () => {
  const legacy = { strategy: "adaptive", route: "/upload", options: { language: "ko" } } as const;
  let constructorCalls = 0;
  const selected = selectPresentationFlow(legacy, () => {
    constructorCalls += 1;
    return createAuthoredVnextFlowState();
  }, undefined);

  assert.equal(selected.mode, "legacy");
  assert.equal(constructorCalls, 0);
  if (selected.mode === "legacy") assert.equal(selected.legacy, legacy);
});

test("enabled selection creates an isolated vNext state", () => {
  const selected = selectPresentationFlow({ strategy: "legacy" }, createAuthoredVnextFlowState, "true");
  assert.equal(selected.mode, "authored-vnext");
  assert.equal(selected.featureEnabled, true);
});
