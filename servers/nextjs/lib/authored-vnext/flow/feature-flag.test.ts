import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORED_VNEXT_DEV_SLIDE_COUNT_FEATURE_FLAG,
  authoredVnextSlideCount,
} from "./feature-flag.ts";

test("three-slide harness is an exact development-only opt-in", () => {
  assert.equal(AUTHORED_VNEXT_DEV_SLIDE_COUNT_FEATURE_FLAG, "NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT");
  assert.equal(authoredVnextSlideCount(undefined, "development"), 90);
  assert.equal(authoredVnextSlideCount("3", "development"), 3);
  assert.equal(authoredVnextSlideCount("3", "production"), 90);
  assert.equal(authoredVnextSlideCount("4", "development"), 90);
});
