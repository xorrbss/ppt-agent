import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import test from "node:test";

import {
  trustedCollectorKeyId,
  validateTrustedCollectorAttestations,
} from "./public-service-trusted-collector.mjs";

test("trusted collector binds every required exact-byte report digest to an external Ed25519 key", () => {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const publicKeyPem = publicKey.export({ type: "spki", format: "pem" });
  const reports = {
    sourceManifest: { exactByteSha256: `sha256:${"a".repeat(64)}` },
    immutableBundle: { sourceDigest: `sha256:${"b".repeat(64)}` },
  };
  const attestations = {
    schemaVersion: "public-service-trusted-collector-attestations/v1",
    algorithm: "ed25519",
    keyId: trustedCollectorKeyId(publicKeyPem),
    reports: Object.fromEntries(Object.entries(reports).map(([name, report]) => {
      const sourceDigest = report.sourceDigest || report.exactByteSha256;
      return [name, { sourceDigest,
        signatureBase64: sign(null, Buffer.from(sourceDigest, "utf8"), privateKey).toString("base64") }];
    })),
  };
  const passed = validateTrustedCollectorAttestations({ reports, attestations, publicKeyPem,
    requiredReportNames: Object.keys(reports) });
  assert.equal(passed.pass, true);

  attestations.reports.immutableBundle.sourceDigest = `sha256:${"c".repeat(64)}`;
  const tampered = validateTrustedCollectorAttestations({ reports, attestations, publicKeyPem,
    requiredReportNames: Object.keys(reports) });
  assert.equal(tampered.pass, false);
});

test("self-generated or absent trust roots fail closed", () => {
  const reportDigest = `sha256:${createHash("sha256").update("report").digest("hex")}`;
  const result = validateTrustedCollectorAttestations({
    reports: { immutableBundle: { sourceDigest: reportDigest } },
    attestations: { schemaVersion: "public-service-trusted-collector-attestations/v1",
      algorithm: "ed25519", keyId: "sha256:fake", reports: {} },
    publicKeyPem: null,
    requiredReportNames: ["immutableBundle"],
  });
  assert.equal(result.pass, false);
});
