import { createHash, createPublicKey, verify } from "node:crypto";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;

function check(id, pass, expected, actual, reason = null) {
  return { id, pass, expected, actual, reason: pass ? null : reason };
}

export function trustedCollectorKeyId(publicKeyPem) {
  try {
    const key = createPublicKey(publicKeyPem);
    if (key.asymmetricKeyType !== "ed25519") return null;
    const der = key.export({ type: "spki", format: "der" });
    return `sha256:${createHash("sha256").update(der).digest("hex")}`;
  } catch {
    return null;
  }
}

function reportSourceDigest(report) {
  if (SHA256.test(String(report?.sourceDigest || ""))) return report.sourceDigest;
  if (SHA256.test(String(report?.exactByteSha256 || ""))) return report.exactByteSha256;
  return null;
}

export function validateTrustedCollectorAttestations({
  reports,
  attestations,
  publicKeyPem,
  requiredReportNames,
}) {
  const keyId = trustedCollectorKeyId(publicKeyPem);
  const headerValid = attestations?.schemaVersion === "public-service-trusted-collector-attestations/v1"
    && attestations?.algorithm === "ed25519"
    && keyId !== null
    && attestations?.keyId === keyId
    && attestations?.reports && typeof attestations.reports === "object"
    && !Array.isArray(attestations.reports);
  const checks = [check("trustedCollector.header", headerValid,
    "v1 Ed25519 attestations bound to the configured external trust root",
    { schemaVersion: attestations?.schemaVersion ?? null, algorithm: attestations?.algorithm ?? null,
      expectedKeyId: keyId, actualKeyId: attestations?.keyId ?? null }, "collector_attestation_header_invalid")];
  if (!headerValid) return { pass: false, keyId, checks };

  const key = createPublicKey(publicKeyPem);
  for (const name of requiredReportNames) {
    const sourceDigest = reportSourceDigest(reports?.[name]);
    const entry = attestations.reports[name];
    const signature = String(entry?.signatureBase64 || "");
    let signatureValid = false;
    if (sourceDigest && entry?.sourceDigest === sourceDigest && BASE64.test(signature)) {
      try {
        signatureValid = verify(null, Buffer.from(sourceDigest, "utf8"), key, Buffer.from(signature, "base64"));
      } catch {
        signatureValid = false;
      }
    }
    checks.push(check(`trustedCollector.${name}`, signatureValid,
      "external collector signs the exact report byte digest",
      { expectedDigest: sourceDigest, attestedDigest: entry?.sourceDigest ?? null, signatureValid },
      sourceDigest ? "collector_signature_invalid" : "report_exact_byte_digest_missing"));
  }
  return { pass: checks.every((item) => item.pass), keyId, checks };
}
