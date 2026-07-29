import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  PresentationSnapshotMismatchError,
  assertPresentationSnapshotIntegrity,
  normalizeExpectedPresentationSha256,
} from "./presentation-snapshot-integrity.ts";

test("accepts the exact serialized presentation payload", () => {
  const body = JSON.stringify({ id: "deck-1", slides: [{ index: 0 }] });
  const sha256 = createHash("sha256").update(body).digest("hex");

  assert.doesNotThrow(() =>
    assertPresentationSnapshotIntegrity(body, sha256.toUpperCase())
  );
});

test("rejects a presentation payload from a different revision", () => {
  const original = JSON.stringify({ id: "deck-1", title: "before" });
  const changed = JSON.stringify({ id: "deck-1", title: "after" });
  const expected = createHash("sha256").update(original).digest("hex");

  assert.throws(
    () => assertPresentationSnapshotIntegrity(changed, expected),
    PresentationSnapshotMismatchError
  );
});

test("rejects malformed expected hashes before rendering", () => {
  assert.throws(
    () => normalizeExpectedPresentationSha256("not-a-sha"),
    /64 hexadecimal/
  );
});
