import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORED_HYBRID_EXPORT_METADATA_SCHEMA_VERSION,
  summarizeAuthoredHybridExport,
} from "./export-metadata.ts";

test("summarizes authored-hybrid quality without exposing element-level detail", () => {
  const metadata = summarizeAuthoredHybridExport({
    schemaVersion: "presenton.export-quality/v1",
    mode: "hybrid",
    status: "partially-editable",
    totalSlides: 3,
    editableSlides: 2,
    imageFallbackSlides: 1,
    nativeTextElements: 4,
    nativeShapeElements: 1,
    nativeImageElements: 0,
    rasterFallbackElements: 2,
    fallbackReasonCounts: { "clip-path": 2 },
    slides: [],
    fontEmbedding: false,
  });

  assert.deepEqual(metadata, {
    schemaVersion: AUTHORED_HYBRID_EXPORT_METADATA_SCHEMA_VERSION,
    strategy: "authored-hybrid",
    status: "partially-editable",
    totalSlides: 3,
    editableSlides: 2,
    imageFallbackSlides: 1,
    fallbackReasonCounts: { "clip-path": 2 },
  });
  assert.equal("slides" in metadata, false);
});
