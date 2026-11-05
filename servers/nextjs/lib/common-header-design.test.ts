import assert from "node:assert/strict";
import test from "node:test";

import { commonHeaderDesignForConfig, hasCommonHeader } from "./common-header-design.ts";

test("the reduced header is a common header without a design input", () => {
  assert.equal(hasCommonHeader(undefined), true);
  assert.equal(hasCommonHeader("uniform"), true);
  assert.equal(hasCommonHeader("reduced"), true);
  assert.equal(hasCommonHeader("free"), false);
  const design = { instructions: "navy band" };
  assert.deepEqual(commonHeaderDesignForConfig({ commonHeaderMode: "uniform", commonHeaderDesign: design }), design);
  assert.equal(commonHeaderDesignForConfig({ commonHeaderMode: "reduced", commonHeaderDesign: design }), undefined);
});
