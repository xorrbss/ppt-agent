import assert from "node:assert/strict";
import test from "node:test";

import { qualityRejectionLabel } from "./qualityRejectionLabels.ts";

test("a slide that never got a repair reads differently from one that failed", () => {
  // The distinction the card could not previously make: "수정 조치: none" was
  // shown for both, so a reader could not tell them apart.
  assert.equal(
    qualityRejectionLabel("deterministic_no_candidate"),
    "복구안을 만들지 못했습니다",
  );
  assert.equal(
    qualityRejectionLabel("constraint.readability.violated"),
    "고친 뒤에도 가독성 기준에 못 미쳤습니다",
  );
  assert.equal(
    qualityRejectionLabel("render.source_projection.roundtrip_failed"),
    "복구안을 원본에 되돌려 적용하지 못했습니다",
  );
});

test("a slide that was never inspected is not reported as defect-free", () => {
  assert.equal(
    qualityRejectionLabel("dom.telemetry_incomplete"),
    "화면 측정이 불완전해 판정하지 못했습니다",
  );
  assert.notEqual(
    qualityRejectionLabel("dom.telemetry_incomplete"),
    qualityRejectionLabel("legacy_preserved.no_geometry_trigger"),
  );
});

test("every code the repair pipeline can emit has a reading", () => {
  // Mirrors NO_CANDIDATE_REASONS plus the canonical issue codes in
  // servers/fastapi/utils/llm_calls/slidespec_repair_resilience.py.
  const emitted = [
    "deterministic_no_candidate",
    "llm_provider_error",
    "llm_empty",
    "llm_schema_rejected",
    "proposal_preflight_unprojectable",
    "negative_cache_hit",
    "dom.clipping",
    "dom.overflow",
    "dom.off_canvas",
    "dom.unintended_overlap",
    "constraint.containment.violated",
    "constraint.non_overlap.violated",
    "constraint.gap.violated",
    "constraint.alignment.violated",
    "constraint.order.violated",
    "constraint.readability.violated",
    "constraint.evidence.missing",
  ];

  const unlabelled = emitted.filter((code) => !qualityRejectionLabel(code));
  assert.deepEqual(unlabelled, []);
});

test("an unmapped code is left for the caller to show verbatim", () => {
  // The card falls back to the raw code, so a new backend reason still reaches
  // the reader instead of silently rendering as an empty row.
  assert.equal(qualityRejectionLabel("some.future.code"), undefined);
});
