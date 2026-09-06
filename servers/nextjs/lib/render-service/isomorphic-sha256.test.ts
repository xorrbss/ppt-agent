import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import test from "node:test";

import { decodeBase64, encodeBase64, sha256Hex } from "./isomorphic-sha256.ts";

// Lengths around every padding boundary: empty, one short of the length field,
// exactly at it, one over, and whole-block multiples.
const BOUNDARY_LENGTHS = [0, 1, 54, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 1000];

test("sha256Hex matches node:crypto across padding boundaries", () => {
  for (const length of BOUNDARY_LENGTHS) {
    const bytes = randomBytes(length);
    assert.equal(
      sha256Hex(new Uint8Array(bytes)),
      createHash("sha256").update(bytes).digest("hex"),
      `digest mismatch for ${length} bytes`,
    );
  }
});

test("sha256Hex matches node:crypto for byte values that stress sign handling", () => {
  const bytes = new Uint8Array(256);
  for (let index = 0; index < 256; index += 1) bytes[index] = index;
  assert.equal(
    sha256Hex(bytes),
    createHash("sha256").update(Buffer.from(bytes)).digest("hex"),
  );
});

test("base64 helpers round-trip and agree with Buffer", () => {
  for (const length of BOUNDARY_LENGTHS) {
    const bytes = randomBytes(length);
    const encoded = encodeBase64(new Uint8Array(bytes));
    assert.equal(encoded, bytes.toString("base64"), `encode mismatch for ${length} bytes`);
    assert.deepEqual(
      decodeBase64(encoded),
      new Uint8Array(bytes),
      `decode mismatch for ${length} bytes`,
    );
  }
});

test("decodeBase64 reports malformed input instead of throwing", () => {
  assert.equal(decodeBase64("not base64!!"), null);
});
