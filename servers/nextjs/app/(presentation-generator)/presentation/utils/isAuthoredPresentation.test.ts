import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  inferSlideDriver,
  isAuthoredPresentation,
  isAuthoredSlide,
} from "./isAuthoredPresentation";

const directory = path.dirname(fileURLToPath(import.meta.url));

test("svg slides are authored-family slides on the svg-v1 driver", () => {
  assert.equal(inferSlideDriver({ layout_group: "svg" }), "svg-v1");
  assert.equal(isAuthoredSlide({ layout_group: "svg" }), true);
  assert.equal(isAuthoredPresentation({ slides: [{ layout_group: "svg" }] }), true);
});

test("authored slides resolve by layout_group or the content sentinel", () => {
  assert.equal(inferSlideDriver({ layout_group: "authored" }), "authored");
  assert.equal(inferSlideDriver({ content: { __authored__: true } }), "authored");
  assert.equal(isAuthoredSlide({ content: { __authored__: true } }), true);
});

test("other slides are neither", () => {
  assert.equal(inferSlideDriver(null), null);
  assert.equal(inferSlideDriver({ layout_group: "general" }), null);
  assert.equal(isAuthoredSlide({ content: { __authored__: false } }), false);
  assert.equal(isAuthoredPresentation({ slides: [{ layout_group: "general" }] }), false);
});

test("PresentationContentRender infers the driver through the shared helper", async () => {
  const source = await fs.readFile(
    path.join(directory, "..", "..", "components", "PresentationContentRender.tsx"),
    "utf8",
  );
  assert.match(source, /inferSlideDriver\(slide\)/);
  assert.doesNotMatch(source, /layout_group === "authored"/);
});
