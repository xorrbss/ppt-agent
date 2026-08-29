import assert from "node:assert/strict";
import test from "node:test";
import sharp from "sharp";

import {
  buildRasterPdfFromJpegs,
  buildRasterPdfFromPngs,
  rasterPdfPageCount,
} from "./raster-pdf.ts";

test("raster PDF owns exactly one page per isolated slide image", async () => {
  const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);
  const pdf = buildRasterPdfFromJpegs([
    { jpeg, width: 1280, height: 720 },
    { jpeg, width: 1280, height: 720 },
    { jpeg, width: 1280, height: 720 },
  ]);

  assert.equal(rasterPdfPageCount(pdf), 3);
  assert.equal((pdf.toString("latin1").match(/\/Subtype \/Image/g) ?? []).length, 3);
  assert.match(pdf.subarray(-80).toString("ascii"), /startxref\n\d+\n%%EOF/);

  const onePixelPng = await sharp({
    create: {
      width: 1,
      height: 1,
      channels: 4,
      background: "#ffffff",
    },
  }).png().toBuffer();
  const converted = await buildRasterPdfFromPngs([onePixelPng, onePixelPng]);
  assert.equal(rasterPdfPageCount(converted), 2);
});
