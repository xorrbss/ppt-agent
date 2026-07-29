import assert from "node:assert/strict";
import test from "node:test";

import { resolvePptxMode, resolveRequestedPptxMode } from "./mode.ts";
import { isAuthoredPresentation } from "./presentation-mode.ts";

test("missing and explicit fidelity modes retain the historical exporter path", () => {
  assert.deepEqual(resolvePptxMode(undefined), { ok: true, value: "fidelity" });
  assert.deepEqual(resolvePptxMode("fidelity"), {
    ok: true,
    value: "fidelity",
  });
  assert.deepEqual(resolvePptxMode("hybrid"), { ok: true, value: "hybrid" });
  assert.deepEqual(resolvePptxMode(null), { ok: false });
  assert.deepEqual(resolvePptxMode("editable"), { ok: false });
});

test("PDF ignores PPTX-only options", () => {
  assert.deepEqual(resolveRequestedPptxMode("pdf", "invalid"), {
    ok: true,
    value: "fidelity",
  });
});

test("authored identity honors explicit mode and legacy sentinels", () => {
  assert.equal(isAuthoredPresentation({ mode: "authored" }), true);
  assert.equal(
    isAuthoredPresentation({ mode: "template", theme: { mode: "authored" } }),
    false
  );
  assert.equal(
    isAuthoredPresentation({ theme: { mode: "authored" }, slides: [] }),
    true
  );
  assert.equal(
    isAuthoredPresentation({
      slides: [{ content: { __authored__: true }, html_content: "<p>x</p>" }],
    }),
    true
  );
  assert.equal(
    isAuthoredPresentation({ slides: [{ layout_group: "authored" }] }),
    true
  );
  assert.equal(isAuthoredPresentation({ slides: [{ layout_group: "default" }] }), false);
});
