import assert from "node:assert/strict";
import test from "node:test";

import { buildConversationContext } from "./context.ts";

test("buildConversationContext sends structured viewport and only safe assets", () => {
  const context = buildConversationContext({
    viewportWidth: 1440,
    viewportHeight: 900,
    currentSlideId: "slide-a",
    currentSlideIndex: 2,
    visibleSlideIds: ["slide-a", "slide-b"],
    smartSelection: { html: "<p>Selected</p>", text: "Selected", slideId: "slide-a" },
    assets: [
      { id: "ready", name: "brief.pdf", status: "ready" },
      { id: "scanned", name: "table.xlsx", status: "scanned" },
      { id: "processing", name: "unsafe.csv", status: "processing" },
    ],
  });
  assert.deepEqual(context.viewport, { width: 1440, height: 900, visibleSlideIds: ["slide-a", "slide-b"] });
  assert.deepEqual(context.currentSlide, { slideId: "slide-a", index: 2 });
  assert.deepEqual(context.assetIds, ["ready", "scanned"]);
  assert.equal(context.smartSelection?.text, "Selected");
});

test("does not send an empty smart selection or invalid slide index", () => {
  const context = buildConversationContext({ currentSlideIndex: -1, smartSelection: { html: " ", text: "text" } });
  assert.equal(context.viewport, undefined);
  assert.equal(context.smartSelection, undefined);
});
