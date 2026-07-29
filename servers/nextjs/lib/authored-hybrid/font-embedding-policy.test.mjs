import assert from "node:assert/strict";
import test from "node:test";

import {
  assessEmbeddingLicense,
  inspectSfntFont,
  resolveFontEmbeddingPlan,
} from "./font-embedding-policy.ts";

test("fsType policy permits installable/editable embedding and enforces restrictions", () => {
  assert.deepEqual(assessEmbeddingLicense(0, "full"), {
    allowed: true,
    decision: "allowed-installable",
    fsType: 0,
    noSubsetting: false,
    strategyAllowed: true,
  });
  assert.equal(assessEmbeddingLicense(0x0008, "full").decision, "allowed-editable");
  assert.equal(assessEmbeddingLicense(0x0002, "full").allowed, false);
  assert.equal(assessEmbeddingLicense(0x0004, "full").allowed, false);
  assert.equal(assessEmbeddingLicense(0x0200, "full").allowed, false);
  assert.equal(assessEmbeddingLicense(0x000a, "full").decision, "denied-invalid");
  assert.equal(assessEmbeddingLicense(0x0100, "subset").strategyAllowed, false);
  assert.equal(assessEmbeddingLicense(0x0100, "full").strategyAllowed, true);
});

test("SFNT inspection rejects non-font and truncated inputs", () => {
  assert.throws(() => inspectSfntFont(Buffer.from("not a font")), /too small/);
  const truncated = Buffer.alloc(12);
  truncated.writeUInt32BE(0x00010000, 0);
  truncated.writeUInt16BE(1, 4);
  assert.throws(() => inspectSfntFont(truncated), /table directory/);
});

test("embedding plan is off unless explicitly requested", async () => {
  const plan = await resolveFontEmbeddingPlan();
  assert.equal(plan.requested, false);
  assert.equal(plan.status.reason, "not-requested");
  assert.equal(plan.files.length, 0);
  assert.equal(plan.faces.length, 0);
  assert.equal(plan.cacheDiscriminator, "font-embedding=off");
});

test("browser-controlled family names cannot escape the server allowlist", async () => {
  const plan = await resolveFontEmbeddingPlan({
    requested: true,
    families: ["../../Windows/Fonts/arial.ttf", "https://example.com/font.ttf"],
  });
  assert.equal(plan.status.eligible, false);
  assert.deepEqual(
    plan.status.failures.map((failure) => failure.reason),
    ["family-not-allowlisted", "family-not-allowlisted"]
  );
  assert.equal(plan.files.length, 0);
});

test(
  "allowlisted local Noto Sans KR source is strictly inspected when present",
  { skip: process.platform !== "win32" },
  async (context) => {
    const plan = await resolveFontEmbeddingPlan({
      requested: true,
      families: ["Noto Sans KR"],
      strategy: "full",
      allowVariableFonts: true,
    });
    if (!plan.status.eligible) {
      context.skip("allowlisted Noto Sans KR source is not installed");
      return;
    }
    assert.equal(plan.files.length >= 1, true);
    assert.equal(plan.faces.some((face) => face.face === "regular"), true);
    assert.equal(plan.faces.some((face) => face.face === "bold"), true);
    for (const file of plan.files) {
      assert.match(file.sha256, /^[a-f0-9]{64}$/);
      assert.equal(file.inspection.familyNames.includes("Noto Sans KR"), true);
      assert.equal(file.license.allowed, true);
      assert.equal(file.inspection.fsType, 0);
    }
    assert.match(
      plan.cacheDiscriminator,
      /^font-embedding=full:(?:server-font-allowlist|local-derived-static):/
    );
  }
);
