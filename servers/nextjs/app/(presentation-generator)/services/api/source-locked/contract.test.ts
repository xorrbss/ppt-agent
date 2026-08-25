import assert from "node:assert/strict";
import test from "node:test";

import {
  sourceLockedGenerationFromResponse,
  type SourceLockedGenerationResponseV1,
} from "./contract.ts";

const artifactId = `sla_${"a".repeat(64)}`;
const sha256 = `sha256:${"b".repeat(64)}`;

function legacyPptResponse(renderedPageCount: number): SourceLockedGenerationResponseV1 {
  return {
    admission_contract: {},
    source_snapshot: {},
    manifest: { missing_content_count: 0 },
    analysis_seal: {},
    progress_stages: [
      { stage: "source_analysis", status: "completed" },
      { stage: "slide_image_restoration", status: "completed" },
      { stage: "missing_content_validation", status: "completed" },
    ],
    fallback_allowed: false,
    summary: {
      source_type: "ppt",
      page_count: 33,
      slide_count: 33,
      media_asset_count: 0,
      image_occurrence_count: 0,
      inherited_occurrence_count: 0,
    },
    generation_record_id: "generation-1",
    artifact: {
      contract_version: "source-locked-artifact/v1",
      artifact_id: artifactId,
      filename: `source-locked-${"c".repeat(16)}.pptx`,
      media_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      byte_length: 1,
      sha256,
      download_url: `/api/v1/ppt/source-locked/v1/artifacts/${artifactId}`,
      created_at: "2026-08-25T00:00:00Z",
      source_type: "ppt",
      slide_count: 33,
    },
    restoration_verification: {
      contract_version: "source-locked-restoration-verification/v1",
      output_package_sha256: sha256,
      slide_count: 33,
      rendered_page_count: renderedPageCount,
      missing_content_count: 0,
      byte_exact_source_package: false,
      fallback_allowed: false,
    },
    idempotent_replay: false,
  };
}

test("legacy .ppt accepts only the backend's zero-rendered-page receipt", () => {
  assert.ok(sourceLockedGenerationFromResponse(legacyPptResponse(0)));
  assert.equal(sourceLockedGenerationFromResponse(legacyPptResponse(33)), null);
});
