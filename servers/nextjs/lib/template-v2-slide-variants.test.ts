import assert from "node:assert/strict";
import test from "node:test";

import {
  applyTemplateV2SlideVariant,
  cancelTemplateV2SlideVariants,
  previewTemplateV2SlideVariants,
  restoreTemplateV2SlideVariant,
} from "./template-v2-slide-variants.ts";

function layouts() {
  return {
    layouts: [
      {
        id: "quarterly",
        server_owned_metadata: { keep: true },
        components: [
          {
            id: "content",
            elements: [
              {
                type: "text",
                name: "title",
                font: { bold: false, size: 24 },
                runs: [{ text: "Quarterly results" }],
              },
              {
                type: "image",
                name: "hero",
                data: "/app_data/images/hero.png",
                fit: "contain",
                unknown_image_metadata: { keep: true },
              },
              {
                type: "chart",
                name: "trend",
                legend: false,
                categories: ["Q1", "Q2"],
                series: [{ name: "Revenue", values: [10, 12] }],
              },
            ],
          },
        ],
      },
    ],
    unknown_envelope_metadata: { keep: [1, 2, 3] },
  };
}

test("previews two or three deterministic slide-scoped candidates", () => {
  const source = layouts();
  const result = previewTemplateV2SlideVariants({
    layouts: source,
    layoutId: "quarterly",
    sourceRevision: 11,
  });
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(
    result.value.candidates.map((candidate) => candidate.kind),
    ["data_focused", "image_focused", "executive_summary"]
  );
  assert.equal(
    new Set(
      result.value.candidates.map((candidate) => candidate.semanticDigest)
    ).size,
    1
  );
  assert.equal(
    new Set(result.value.candidates.map((candidate) => candidate.renderDigest))
      .size,
    3
  );
  assert.deepEqual(cancelTemplateV2SlideVariants(result.value), {
    previewId: result.value.id,
    status: "cancelled",
  });
  assert.deepEqual(source, layouts());
});

test("applies one bounded visual patch and restores from its journal snapshot", () => {
  const source = layouts();
  const preview = previewTemplateV2SlideVariants({
    layouts: source,
    layoutId: "quarterly",
    sourceRevision: 11,
  });
  assert.equal(preview.ok, true);
  if (!preview.ok) return;
  const applied = applyTemplateV2SlideVariant({
    layouts: source,
    preview: preview.value,
    selectedKind: "executive_summary",
    expectedRevision: 11,
    currentRevision: 11,
  });
  assert.equal(applied.ok, true);
  if (!applied.ok) return;
  const element = (
    applied.value.layouts.layouts as Array<{
      components: Array<{ elements: Array<Record<string, unknown>> }>;
    }>
  )[0].components[0].elements[0];
  assert.deepEqual(element.font, { bold: true, size: 24 });
  assert.deepEqual(element.runs, [{ text: "Quarterly results" }]);
  assert.deepEqual(applied.value.layouts.unknown_envelope_metadata, {
    keep: [1, 2, 3],
  });
  const restored = restoreTemplateV2SlideVariant({
    layouts: applied.value.layouts,
    journalEntry: applied.value.journalEntry,
    expectedRevision: 12,
    currentRevision: 12,
  });
  assert.equal(restored.ok, true);
  if (restored.ok) assert.deepEqual(restored.value.layouts, source);
});

test("fails closed for stale revisions, changed previews, and sparse slides", () => {
  const source = layouts();
  const preview = previewTemplateV2SlideVariants({
    layouts: source,
    layoutId: "quarterly",
    sourceRevision: 4,
  });
  assert.equal(preview.ok, true);
  if (!preview.ok) return;
  assert.deepEqual(
    applyTemplateV2SlideVariant({
      layouts: source,
      preview: preview.value,
      selectedKind: "data_focused",
      expectedRevision: 3,
      currentRevision: 4,
    }),
    { ok: false, code: "template_v2_variant_stale_revision" }
  );
  const changed = structuredClone(source);
  const changedRun =
    changed.layouts.at(0)?.components.at(0)?.elements.at(0)?.runs?.at(0);
  assert.ok(changedRun);
  changedRun!.text = "Changed";
  assert.deepEqual(
    applyTemplateV2SlideVariant({
      layouts: changed,
      preview: preview.value,
      selectedKind: "data_focused",
      expectedRevision: 4,
      currentRevision: 4,
    }),
    { ok: false, code: "template_v2_variant_preview_stale" }
  );
  assert.deepEqual(
    previewTemplateV2SlideVariants({
      layouts: {
        layouts: [{ id: "sparse", components: [{ id: "c", elements: [] }] }],
      },
      layoutId: "sparse",
      sourceRevision: 4,
    }),
    { ok: false, code: "template_v2_variant_candidate_count_invalid" }
  );
});
