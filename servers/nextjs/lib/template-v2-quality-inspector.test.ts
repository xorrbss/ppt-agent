import assert from "node:assert/strict";
import test from "node:test";

import {
  TemplateV2QualityInspectorError,
  applyTemplateV2QualityFix,
  inspectTemplateV2Quality,
  previewTemplateV2QualityFix,
  type TemplateV2QualityReasonCode,
} from "./template-v2-quality-inspector.ts";
import type { JsonRecord } from "./template-v2-studio.ts";

function cell(value: string): JsonRecord {
  return { runs: [{ text: value }] };
}

function fixture(): JsonRecord {
  const columns = Array.from({ length: 9 }, (_, index) =>
    cell(`Column ${index}`)
  );
  const denseElements = Array.from({ length: 25 }, (_, index) => ({
    type: "image",
    name: `decorative-${index}`,
    decorative: true,
    data: `/app_data/images/${index}.png`,
    is_icon: false,
  }));
  return {
    layouts: [
      {
        id: "quality-slide",
        description: "Quality fixture",
        vendor_layout_metadata: { keep: ["exactly", 1] },
        components: [
          {
            id: "content",
            description: "Quality content",
            elements: [
              {
                type: "text",
                name: "title",
                decorative: false,
                runs: [
                  {
                    text: "This title is deliberately long",
                    font: {
                      size: 7,
                      color: "#777777",
                      vendor_font_token: "title-muted",
                    },
                  },
                ],
                fill: { color: "#888888" },
                max_length: 10,
                min_length: 1,
                vendor_element_metadata: { keep: "yes" },
              },
              {
                type: "chart",
                name: "trend",
                decorative: false,
                chart_type: "line",
                categories: ["Q1", "Q2"],
                series: [
                  { name: "Actual", values: [1, 2] },
                  { name: "Plan", values: [2, 3] },
                ],
                legend: false,
              },
              {
                type: "table",
                name: "wide-table",
                decorative: false,
                columns,
                rows: [structuredClone(columns)],
              },
              {
                type: "image",
                name: "legacy-render",
                decorative: false,
                data: "/app_data/images/legacy.png",
                is_icon: false,
                raster_only: true,
                compatibility: { unsupported_reason: "legacy_effect" },
              },
            ],
          },
        ],
      },
      {
        id: "dense-slide",
        description: "Dense fixture",
        components: [
          {
            id: "dense-content",
            description: "Too many leaves",
            elements: denseElements,
          },
        ],
      },
    ],
    vendor_envelope_metadata: { keep: { nested: true } },
  };
}

test("quality inspection is deterministic, complete, and non-mutating", () => {
  const layouts = fixture();
  const original = structuredClone(layouts);
  const first = inspectTemplateV2Quality(layouts);
  const second = inspectTemplateV2Quality(layouts);

  assert.deepEqual(first, second);
  assert.deepEqual(layouts, original);
  assert.deepEqual(
    new Set(first.findings.map((finding) => finding.reasonCode)),
    new Set<TemplateV2QualityReasonCode>([
      "TEXT_OVERFLOW",
      "TEXT_BELOW_9PT",
      "TEXT_LOW_CONTRAST",
      "SLIDE_OVERDENSE",
      "CHART_UNIT_UNSPECIFIED",
      "CHART_LEGEND_MISSING",
      "TABLE_TOO_MANY_COLUMNS",
      "ELEMENT_UNSUPPORTED",
      "ELEMENT_RASTER_ONLY",
    ])
  );
  assert.equal(
    new Set(first.findings.map((finding) => finding.id)).size,
    first.findings.length
  );
});

test("each safe fix requires its own preview and explicit revision-CAS apply", () => {
  const layouts = fixture();
  const inspection = inspectTemplateV2Quality(layouts);
  const finding = inspection.findings.find(
    (entry) => entry.reasonCode === "TEXT_BELOW_9PT"
  );
  assert.ok(finding);
  const preview = previewTemplateV2QualityFix({
    layouts,
    inspection,
    findingId: finding.id,
    expectedRevision: 7,
    idempotencyKey: "quality:test:font:0001",
  });

  assert.equal(preview.patch.before, 7);
  assert.equal(preview.patch.after, 9);
  assert.equal(
    ((layouts.layouts as JsonRecord[])[0].components as JsonRecord[])[0]
      .elements instanceof Array,
    true
  );

  const result = applyTemplateV2QualityFix({
    layouts,
    preview,
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey: "quality:test:font:0001",
  });
  const title = (
    (((result.layouts.layouts as JsonRecord[])[0].components as JsonRecord[])[0]
      .elements as JsonRecord[])
  )[0];
  assert.equal(((title.runs as JsonRecord[])[0].font as JsonRecord).size, 9);
  assert.deepEqual(title.vendor_element_metadata, { keep: "yes" });
  assert.equal(
    ((title.runs as JsonRecord[])[0].font as JsonRecord).vendor_font_token,
    "title-muted"
  );
  assert.deepEqual(result.layouts.vendor_envelope_metadata, {
    keep: { nested: true },
  });
  assert.equal(result.revision, 8);
  assert.deepEqual(result.autosave, {
    expected_revision: 7,
    idempotency_key: "quality:test:font:0001",
  });
  assert.deepEqual(layouts, fixture());
});

test("unsafe findings remain inspection-only", () => {
  const layouts = fixture();
  const inspection = inspectTemplateV2Quality(layouts);
  const overflow = inspection.findings.find(
    (entry) => entry.reasonCode === "TEXT_OVERFLOW"
  );
  assert.ok(overflow);
  assert.equal(overflow.safeFixAvailable, false);
  assert.throws(
    () =>
      previewTemplateV2QualityFix({
        layouts,
        inspection,
        findingId: overflow.id,
        expectedRevision: 7,
        idempotencyKey: "quality:test:unsafe:0001",
      }),
    (error) =>
      error instanceof TemplateV2QualityInspectorError &&
      error.code === "template_v2_quality_fix_unavailable"
  );
});

test("stale source, stale revision, idempotency mismatch, and tampering fail closed", () => {
  const layouts = fixture();
  const inspection = inspectTemplateV2Quality(layouts);
  const legend = inspection.findings.find(
    (entry) => entry.reasonCode === "CHART_LEGEND_MISSING"
  );
  assert.ok(legend);
  const preview = previewTemplateV2QualityFix({
    layouts,
    inspection,
    findingId: legend.id,
    expectedRevision: 11,
    idempotencyKey: "quality:test:legend:0001",
  });

  assert.throws(
    () =>
      applyTemplateV2QualityFix({
        layouts,
        preview,
        expectedRevision: 10,
        currentRevision: 11,
        idempotencyKey: "quality:test:legend:0001",
      }),
    /template_v2_quality_stale_revision/
  );
  assert.throws(
    () =>
      applyTemplateV2QualityFix({
        layouts,
        preview,
        expectedRevision: 11,
        currentRevision: 11,
        idempotencyKey: "quality:test:legend:other",
      }),
    /template_v2_quality_idempotency_conflict/
  );

  const changed = structuredClone(layouts);
  (changed.vendor_envelope_metadata as JsonRecord).changed = true;
  assert.throws(
    () =>
      applyTemplateV2QualityFix({
        layouts: changed,
        preview,
        expectedRevision: 11,
        currentRevision: 11,
        idempotencyKey: "quality:test:legend:0001",
      }),
    /template_v2_quality_preview_stale/
  );

  const tampered = structuredClone(preview);
  tampered.patch.after = false;
  assert.throws(
    () =>
      applyTemplateV2QualityFix({
        layouts,
        preview: tampered,
        expectedRevision: 11,
        currentRevision: 11,
        idempotencyKey: "quality:test:legend:0001",
      }),
    /template_v2_quality_preview_tampered/
  );
});

test("malformed recursive layouts fail closed before inspection", () => {
  assert.throws(
    () =>
      inspectTemplateV2Quality({
        layouts: [
          {
            id: "bad",
            components: [{ id: "content", elements: [{ name: "missing-type" }] }],
          },
        ],
      }),
    /template_v2_quality_layouts_invalid/
  );
});
