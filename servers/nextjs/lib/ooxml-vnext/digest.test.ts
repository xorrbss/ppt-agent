import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalJson,
  localeStableDigest,
  prefixedSha256,
  sha256,
  stableDigest,
} from "./digest.ts";

// Golden values computed with node:crypto from the per-file copies these
// helpers replaced (2026-09-29). They are persisted digests: do not update
// them without versioning the receipts that carry them.

test("sha256 matches createHash for bytes and UTF-8 strings", () => {
  assert.equal(
    sha256(new Uint8Array([0, 1, 2, 255])),
    "3d1f57c984978ef98a18378c8166c1cb8ede02c03eeb6aee7e2f121dfeee3e56",
  );
  assert.equal(
    sha256("h\u00e9llo"),
    "3c48591d8d098a4538f5e013dfcf406e948eac4d3277b10bf614e295d6068179",
  );
  assert.equal(
    sha256(Buffer.from("abc")),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
  assert.equal(
    prefixedSha256("abc"),
    "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
});

test("stableDigest frames records after a code-unit sort", () => {
  const records = ["b", "a", "\u00c9", "z\u0000"];
  assert.equal(
    stableDigest(records),
    "b0eb36b8ebc1e4faf4b215d9e22df2ccdeedeb64342bfe56a2e4605a5329b2c0",
  );
  assert.equal(stableDigest([...records].reverse()), stableDigest(records));
});

test("localeStableDigest keeps the master-inventory ordering fork", () => {
  const records = ["a", "B", "c"];
  assert.equal(
    stableDigest(records),
    "643a5a7095a4522fbbbed1872408bcbc49b201216b0072e52f87626264e2a700",
  );
  assert.equal(
    localeStableDigest(records),
    "e58f41ce0e9e83fe153cb71eb843e879ca8d2d57e8053cfe0bfeea5924783e52",
  );
});

test("canonicalJson sorts keys by code unit and rejects unencodable values", () => {
  const value = { b: 1, a: [1, { d: 2, c: null }], s: "\u00e9" };
  assert.equal(canonicalJson(value), '{"a":[1,{"c":null,"d":2}],"b":1,"s":"\u00e9"}');
  assert.equal(
    sha256(canonicalJson(value)),
    "5a942300c44559dd41ec574ae488b704e707af1401e001f3d6c3ae3033389a8e",
  );
  assert.throws(() => canonicalJson(undefined), /not canonical JSON/);
});
