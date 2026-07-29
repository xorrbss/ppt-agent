import assert from "node:assert/strict";
import test from "node:test";

import {
  constrainCropImageFrame,
  cropDraftFromImageFrame,
  cropImageFrameForDraft,
  cropScaleFromProperties,
  imageCropDomStyle,
  imageCropProperties,
  normalizeImageCropScale,
  resolveImageEditorProperties,
} from "./imageCrop.ts";

test("crop scale accepts zoom-out values and clamps to the upstream range", () => {
  assert.equal(normalizeImageCropScale(0.5), 0.5);
  assert.equal(normalizeImageCropScale(0.01), 0.1);
  assert.equal(normalizeImageCropScale(8), 6);
  assert.equal(normalizeImageCropScale(undefined), 1);
});

test("legacy indexed and direct image properties re-enter with the same crop", () => {
  const legacy = [
    {
      initialObjectFit: "cover",
      initialFocusPoint: { x: 25, y: 75 },
      cropScale: 0.5,
    },
  ];
  const direct = {
    initialObjectFit: "cover",
    initialFocusPoint: { x: 25, y: 75 },
    crop_scale: 0.5,
  };

  assert.equal(cropScaleFromProperties(resolveImageEditorProperties(legacy, 0)), 0.5);
  assert.equal(cropScaleFromProperties(resolveImageEditorProperties(direct, 0)), 0.5);
  assert.deepEqual(imageCropProperties("cover", { x: 25, y: 75 }, 0.5), {
    initialObjectFit: "cover",
    initialFocusPoint: { x: 25, y: 75 },
    crop_scale: 0.5,
  });
  assert.equal(
    imageCropDomStyle("cover", { x: 25, y: 75 }, 0.5).transform,
    "scale(0.5)"
  );
});

test("zoomed-out crop geometry uses free space and round-trips focus coordinates", () => {
  const cropFrame = { left: 10, top: 20, width: 100, height: 100 };
  const imageFrame = cropImageFrameForDraft(
    cropFrame,
    { width: 100, height: 100 },
    { x: 75, y: 50, scale: 0.5 }
  );

  assert.deepEqual(imageFrame, {
    left: 47.5,
    top: 45,
    width: 50,
    height: 50,
  });
  assert.deepEqual(cropDraftFromImageFrame(cropFrame, imageFrame, 0.5), {
    x: 75,
    y: 50,
    scale: 0.5,
  });

  assert.deepEqual(
    constrainCropImageFrame(cropFrame, {
      ...imageFrame,
      left: -100,
      top: 200,
    }),
    { ...imageFrame, left: 10, top: 70 }
  );
});

test("zoomed-in crop coordinates remain constrained to overflow bounds", () => {
  const cropFrame = { left: 0, top: 0, width: 100, height: 100 };
  const constrained = constrainCropImageFrame(cropFrame, {
    left: -200,
    top: 50,
    width: 200,
    height: 150,
  });
  assert.deepEqual(constrained, {
    left: -100,
    top: 0,
    width: 200,
    height: 150,
  });
  assert.deepEqual(cropDraftFromImageFrame(cropFrame, constrained, 2), {
    x: 100,
    y: 0,
    scale: 2,
  });
});
