import assert from "node:assert/strict";
import test from "node:test";

import {
  TEMPLATE_V2_IMAGE_MAX_BYTES,
  TEMPLATE_V2_LOCAL_ASSET_METADATA_KEY,
  applyTemplateV2ImageReplacement,
  applyTemplateV2ImageReplacementPatch,
  createTemplateV2ImageReplacementPreview,
  validateTemplateV2LocalImage,
} from "./template-v2-image-replacement.ts";

function pngHeader(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  new DataView(bytes.buffer).setUint32(16, width);
  new DataView(bytes.buffer).setUint32(20, height);
  return bytes;
}

function imageElement() {
  return {
    type: "image",
    data: "/app_data/images/original.png",
    fit: "cover",
    focus_x: 44,
    focus_y: 55,
    crop_scale: 1.1,
    position: { x: 10, y: 20, vendor_position: "preserved" },
    size: { width: 640, height: 360 },
    is_icon: false,
    vendor_extension: { keep: true },
  };
}

test("validates PNG magic, byte size, dimensions, pixels, and provenance", async () => {
  const result = await validateTemplateV2LocalImage({
    filename: "C:\\fakepath\\safe.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(1200, 600),
  });
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.asset.filename, "safe.png");
  assert.equal(result.asset.mediaType, "image/png");
  assert.equal(result.asset.width, 1200);
  assert.equal(result.asset.height, 600);
  assert.equal(result.asset.pixelCount, 720_000);
  assert.match(result.asset.assetId, /^local-[0-9a-f]{24}$/);
  assert.match(result.asset.sha256, /^[0-9a-f]{64}$/);
  assert.match(result.asset.dataUrl, /^data:image\/png;base64,/);
});

test("fails closed for remote-looking names, forbidden MIME, and magic mismatch", async () => {
  const remote = await validateTemplateV2LocalImage({
    filename: "https://attacker.invalid/a.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(1, 1),
  });
  assert.deepEqual(remote, {
    ok: false,
    code: "template_v2_local_image_filename_invalid",
  });

  const svg = await validateTemplateV2LocalImage({
    filename: "unsafe.svg",
    declaredMediaType: "image/svg+xml",
    bytes: new TextEncoder().encode("<svg/>"),
  });
  assert.deepEqual(svg, {
    ok: false,
    code: "template_v2_local_image_type_not_allowed",
  });

  const mismatch = await validateTemplateV2LocalImage({
    filename: "spoofed.jpg",
    declaredMediaType: "image/jpeg",
    bytes: pngHeader(1, 1),
  });
  assert.deepEqual(mismatch, {
    ok: false,
    code: "template_v2_local_image_magic_mismatch",
  });
});

test("fails closed when the browser decoder rejects or disagrees with header dimensions", async () => {
  const rejected = await validateTemplateV2LocalImage({
    filename: "broken.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(10, 20),
    decode: async () => {
      throw new Error("decode failed");
    },
  });
  assert.deepEqual(rejected, {
    ok: false,
    code: "template_v2_local_image_decode_failed",
  });

  const mismatched = await validateTemplateV2LocalImage({
    filename: "mismatch.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(10, 20),
    decode: async () => ({ width: 10, height: 21 }),
  });
  assert.deepEqual(mismatched, {
    ok: false,
    code: "template_v2_local_image_decode_failed",
  });
});

test("enforces byte, per-dimension, and decoded-pixel limits", async () => {
  const oversized = await validateTemplateV2LocalImage({
    filename: "large.png",
    declaredMediaType: "image/png",
    bytes: new Uint8Array(TEMPLATE_V2_IMAGE_MAX_BYTES + 1),
  });
  assert.equal(
    oversized.ok ? "" : oversized.code,
    "template_v2_local_image_bytes_exceeded",
  );

  const dimensions = await validateTemplateV2LocalImage({
    filename: "wide.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(8193, 1),
  });
  assert.equal(
    dimensions.ok ? "" : dimensions.code,
    "template_v2_local_image_dimension_exceeded",
  );

  const pixels = await validateTemplateV2LocalImage({
    filename: "dense.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(7000, 7000),
  });
  assert.equal(
    pixels.ok ? "" : pixels.code,
    "template_v2_local_image_pixels_exceeded",
  );
});

test("creates three deterministic bounded crop candidates", async () => {
  const validated = await validateTemplateV2LocalImage({
    filename: "landscape.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(1200, 600),
  });
  assert.equal(validated.ok, true);
  if (!validated.ok) return;

  const first = await createTemplateV2ImageReplacementPreview({
    element: imageElement(),
    asset: validated.asset,
    revision: 7,
  });
  const second = await createTemplateV2ImageReplacementPreview({
    element: imageElement(),
    asset: validated.asset,
    revision: 7,
  });
  assert.equal(first.ok, true);
  assert.deepEqual(first, second);
  if (!first.ok) return;
  assert.equal(first.preview.cropCandidates.length, 3);
  assert.deepEqual(
    first.preview.cropCandidates.map((candidate) => ({
      strategy: candidate.strategy,
      reasonCode: candidate.reasonCode,
      focusX: candidate.focusX,
      focusY: candidate.focusY,
      cropScale: candidate.cropScale,
    })),
    [
      {
        strategy: "center",
        reasonCode: "CENTER_SAFE_CROP",
        focusX: 50,
        focusY: 50,
        cropScale: 1,
      },
      {
        strategy: "adaptive_focus",
        reasonCode: "LANDSCAPE_SAFE_CROP",
        focusX: 50,
        focusY: 42,
        cropScale: 1.15,
      },
      {
        strategy: "rule_of_thirds",
        reasonCode: "RULE_OF_THIRDS_CROP",
        focusX: 33.333,
        focusY: 50,
        cropScale: 1.25,
      },
    ],
  );
});

test("applies one bounded patch with provenance and deferred retention", async () => {
  const validated = await validateTemplateV2LocalImage({
    filename: "portrait.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(600, 1200),
  });
  assert.equal(validated.ok, true);
  if (!validated.ok) return;
  const element = imageElement();
  const preview = await createTemplateV2ImageReplacementPreview({
    element,
    asset: validated.asset,
    revision: 4,
  });
  assert.equal(preview.ok, true);
  if (!preview.ok) return;

  const selected = preview.preview.cropCandidates[1];
  const applied = await applyTemplateV2ImageReplacement({
    element,
    preview: preview.preview,
    candidateId: selected.candidateId,
    currentRevision: 4,
  });
  assert.equal(applied.ok, true);
  if (!applied.ok) return;
  assert.deepEqual(
    Object.keys(applied.patch).sort(),
    [
      TEMPLATE_V2_LOCAL_ASSET_METADATA_KEY,
      "crop_scale",
      "data",
      "focus_x",
      "focus_y",
    ].sort(),
  );
  const metadata = applied.patch[TEMPLATE_V2_LOCAL_ASSET_METADATA_KEY];
  assert.equal(metadata.asset_record.provenance.source, "local-upload");
  assert.equal(
    metadata.retention.previous_reference,
    "/app_data/images/original.png",
  );
  assert.equal(metadata.retention.defer_orphan_cleanup, true);
  assert.equal(metadata.retention.delete_immediately, false);
  assert.match(metadata.idempotency_key, /^template-v2-local-image-/);

  const next = applyTemplateV2ImageReplacementPatch(element, applied.patch);
  assert.deepEqual(next.vendor_extension, { keep: true });
  assert.deepEqual(next.position, {
    x: 10,
    y: 20,
    vendor_position: "preserved",
  });
  assert.equal(next.focus_y, 38);
  assert.equal(next.crop_scale, 1.15);
});

test("retains the prior local asset record for later orphan cleanup", async () => {
  const first = await validateTemplateV2LocalImage({
    filename: "first.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(100, 100),
  });
  const second = await validateTemplateV2LocalImage({
    filename: "second.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(101, 100),
  });
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  if (!first.ok || !second.ok) return;
  const firstPreview = await createTemplateV2ImageReplacementPreview({
    element: imageElement(),
    asset: first.asset,
    revision: 1,
  });
  assert.equal(firstPreview.ok, true);
  if (!firstPreview.ok) return;
  const firstApply = await applyTemplateV2ImageReplacement({
    element: imageElement(),
    preview: firstPreview.preview,
    candidateId: firstPreview.preview.cropCandidates[0].candidateId,
    currentRevision: 1,
  });
  assert.equal(firstApply.ok, true);
  if (!firstApply.ok) return;
  const replaced = applyTemplateV2ImageReplacementPatch(
    imageElement(),
    firstApply.patch,
  );
  const secondPreview = await createTemplateV2ImageReplacementPreview({
    element: replaced,
    asset: second.asset,
    revision: 2,
  });
  assert.equal(secondPreview.ok, true);
  if (!secondPreview.ok) return;
  assert.deepEqual(
    secondPreview.preview.retention.previous_asset_record,
    firstPreview.preview.assetRecord,
  );
});

test("rejects stale revisions, stale source elements, unknown candidates, and icons", async () => {
  const validated = await validateTemplateV2LocalImage({
    filename: "safe.png",
    declaredMediaType: "image/png",
    bytes: pngHeader(400, 300),
  });
  assert.equal(validated.ok, true);
  if (!validated.ok) return;
  const element = imageElement();
  const preview = await createTemplateV2ImageReplacementPreview({
    element,
    asset: validated.asset,
    revision: 3,
  });
  assert.equal(preview.ok, true);
  if (!preview.ok) return;

  assert.deepEqual(
    await applyTemplateV2ImageReplacement({
      element,
      preview: preview.preview,
      candidateId: preview.preview.cropCandidates[0].candidateId,
      currentRevision: 4,
    }),
    { ok: false, code: "template_v2_local_image_stale_revision" },
  );
  assert.deepEqual(
    await applyTemplateV2ImageReplacement({
      element: { ...element, data: "/changed.png" },
      preview: preview.preview,
      candidateId: preview.preview.cropCandidates[0].candidateId,
      currentRevision: 3,
    }),
    { ok: false, code: "template_v2_local_image_preview_stale" },
  );
  assert.deepEqual(
    await applyTemplateV2ImageReplacement({
      element,
      preview: preview.preview,
      candidateId: "not-previewed",
      currentRevision: 3,
    }),
    {
      ok: false,
      code: "template_v2_local_image_crop_candidate_unknown",
    },
  );
  assert.deepEqual(
    await createTemplateV2ImageReplacementPreview({
      element: { ...element, is_icon: true },
      asset: validated.asset,
      revision: 3,
    }),
    {
      ok: false,
      code: "template_v2_local_image_crop_unsupported_icon",
    },
  );
});
