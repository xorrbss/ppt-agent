import assert from "node:assert/strict";
import test from "node:test";

import { createInfographicViewModel } from "./infographic-policy.ts";

test("a6552331 uses a slim centered progress model without numeric labels", () => {
  const model = createInfographicViewModel({
    kind: "progress_bar",
    value: 64,
    min_value: 0,
    max_value: 100,
    highlight_color: "#2563eb",
    base_color: "#e5e7eb",
    frame_height: 40,
  });
  assert.equal(model.policy_version, "a6552331");
  assert.equal(model.ratio, 0.64);
  assert.equal(model.progress_track_height, 7);
  assert.equal(model.show_numeric_label, false);
});

test("a6552331 gauge uses the same clamped metric and omits labels", () => {
  const model = createInfographicViewModel({
    kind: "gauge",
    value: 110,
    min_value: 0,
    max_value: 100,
    highlight_color: "#2563eb",
    base_color: "#e5e7eb",
  });
  assert.equal(model.ratio, 1);
  assert.equal(model.show_numeric_label, false);
  assert.throws(
    () => createInfographicViewModel({ ...model, value: 1, min_value: 2, max_value: 1 }),
    /render_infographic_invalid_range/,
  );
});
