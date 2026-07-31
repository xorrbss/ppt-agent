import assert from "node:assert/strict";
import test from "node:test";

import {
  getChatToolLabel,
  isPresentationMutationTool,
  isSlideFocusTool,
} from "./chatToolMetadata.ts";

test("classifies slide mutations and their legacy aliases", () => {
  for (const tool of [
    "saveSlide",
    "updateSlide",
    "addNewSlide",
    "deleteSlide",
    "groupElements",
    "ungroupElements",
    "reorderElement",
    "updateElement",
    "updateSlideElement",
    "resizeElement",
  ]) {
    assert.equal(isPresentationMutationTool(tool), true, tool);
    assert.equal(isSlideFocusTool(tool), true, tool);
  }
});

test("refreshes deck mutations which should not move the selected canvas", () => {
  for (const tool of ["addOutline", "moveOutline", "setPresentationTheme"]) {
    assert.equal(isPresentationMutationTool(tool), true, tool);
    assert.equal(isSlideFocusTool(tool), false, tool);
  }
});

test("keeps read tools read-only and labels aliases safely", () => {
  assert.equal(isPresentationMutationTool("searchSlide"), false);
  assert.equal(isPresentationMutationTool("getPresentationTheme"), false);
  assert.equal(getChatToolLabel("readSourceDocument"), "원본 문서 읽기");
  assert.equal(getChatToolLabel("futureTool"), "futureTool");
});
