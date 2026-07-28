import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_DOCUMENT_UPLOAD_MIB,
  DEFAULT_IMAGE_UPLOAD_MIB,
  HARD_DOCUMENT_UPLOAD_MIB,
  HARD_IMAGE_UPLOAD_MIB,
  getUploadLimits,
} from "./upload-limits.ts";

test("upload limits use safe defaults", () => {
  const limits = getUploadLimits({});
  assert.equal(limits.document.mb, DEFAULT_DOCUMENT_UPLOAD_MIB);
  assert.equal(limits.image.mb, DEFAULT_IMAGE_UPLOAD_MIB);
});

test("upload limits accept configured overrides", () => {
  const limits = getUploadLimits({
    PRESENTON_MAX_UPLOAD_MB: "256",
    PRESENTON_MAX_UPLOAD_TOTAL_MB: "400",
    PRESENTON_MAX_IMAGE_UPLOAD_MB: "32",
  });
  assert.equal(limits.document.mb, 256);
  assert.equal(limits.requestTotal.mb, 400);
  assert.equal(limits.image.mb, 32);
});

test("upload limits clamp excessive values", () => {
  const limits = getUploadLimits({
    PRESENTON_MAX_UPLOAD_MB: "9999",
    PRESENTON_MAX_IMAGE_UPLOAD_MB: "9999",
  });
  assert.equal(limits.document.mb, HARD_DOCUMENT_UPLOAD_MIB);
  assert.equal(limits.image.mb, HARD_IMAGE_UPLOAD_MIB);
});

test("invalid values fail closed to defaults", () => {
  for (const value of ["0", "-1", "1.5", "not-a-number"]) {
    const limits = getUploadLimits({
      PRESENTON_MAX_UPLOAD_MB: value,
      PRESENTON_MAX_IMAGE_UPLOAD_MB: value,
    });
    assert.equal(limits.document.mb, DEFAULT_DOCUMENT_UPLOAD_MIB);
    assert.equal(limits.image.mb, DEFAULT_IMAGE_UPLOAD_MIB);
  }
});

test("request total cannot be configured below one document", () => {
  const limits = getUploadLimits({
    PRESENTON_MAX_UPLOAD_MB: "256",
    PRESENTON_MAX_UPLOAD_TOTAL_MB: "128",
  });
  assert.equal(limits.requestTotal.mb, 256);
});
