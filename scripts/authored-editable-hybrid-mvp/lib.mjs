import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])]),
    );
  }
  if (typeof value === "number" && !Number.isFinite(value)) {
    throw new Error("canonical-json-non-finite-number");
  }
  return value;
}

export function canonicalJson(value) {
  return JSON.stringify(canonicalValue(value));
}

export function sha256Bytes(bytes) {
  return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
}

export function canonicalSha256(value) {
  return sha256Bytes(Buffer.from(canonicalJson(value), "utf8"));
}

export function digestExcluding(value, fields) {
  const clone = structuredClone(value);
  for (const field of fields) delete clone[field];
  return canonicalSha256(clone);
}

export function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

export function writeFreshJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
}

export function ensureFreshDirectory(directory, label) {
  if (fs.existsSync(directory)) throw new Error(`${label}-must-be-fresh`);
  fs.mkdirSync(directory, { recursive: true });
}

export function parsePairs(argv, start = 0) {
  const options = {};
  for (let index = start; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error(`invalid-argument:${key}`);
    options[key.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
  }
  return options;
}

export function assertManifestDigest(manifest, label) {
  if (!DIGEST_RE.test(manifest.manifestDigest || "")) throw new Error(`${label}-manifest-digest-format`);
  if (manifest.manifestDigest !== digestExcluding(manifest, ["manifestDigest"])) {
    throw new Error(`${label}-manifest-digest-mismatch`);
  }
}

export function sortedUniqueStrings(values, label) {
  if (!Array.isArray(values) || values.some((value) => typeof value !== "string" || !value)) {
    throw new Error(`${label}-invalid`);
  }
  const sorted = [...values].sort();
  if (new Set(sorted).size !== sorted.length) throw new Error(`${label}-duplicate`);
  return sorted;
}

export function sameStrings(left, right) {
  return JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
}

export function valueAt(source, dottedPath) {
  return dottedPath.split(".").reduce((value, key) => value?.[key], source);
}

function realFile(filePath, label) {
  let resolved;
  try {
    resolved = fs.realpathSync(filePath);
  } catch {
    throw new Error(`${label}-missing:${filePath}`);
  }
  if (!fs.statSync(resolved).isFile()) throw new Error(`${label}-not-file:${filePath}`);
  return resolved;
}

export function loadArtifact(binding, label = "artifact") {
  if (!binding || typeof binding.artifactId !== "string" || typeof binding.path !== "string") {
    throw new Error(`${label}-binding-invalid`);
  }
  const absolute = realFile(path.resolve(binding.path), `${label}:${binding.artifactId}`);
  const bytes = fs.readFileSync(absolute);
  const sha256 = sha256Bytes(bytes);
  if (binding.sha256 !== undefined && binding.sha256 !== sha256) {
    throw new Error(`${label}-digest-mismatch:${binding.artifactId}`);
  }
  if (binding.byteLength !== undefined && binding.byteLength !== bytes.length) {
    throw new Error(`${label}-length-mismatch:${binding.artifactId}`);
  }
  return {
    artifactId: binding.artifactId,
    versionId: binding.versionId || sha256.replace("sha256:", "sha256-"),
    path: absolute,
    mediaType: binding.mediaType || "application/octet-stream",
    sha256,
    byteLength: bytes.length,
    bytes,
  };
}

export function artifactInventory(records) {
  return records.map(({ artifactId, versionId, sha256, byteLength, mediaType }) => ({
    artifactId,
    versionId,
    sha256,
    byteLength,
    mediaType,
  })).sort((left, right) => left.artifactId.localeCompare(right.artifactId));
}

export function pngDimensions(bytes, label) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (bytes.length < 24 || !bytes.subarray(0, 8).equals(signature) || bytes.toString("ascii", 12, 16) !== "IHDR") {
    throw new Error(`${label}-png-invalid`);
  }
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

export function pptxSlideCount(bytes, label) {
  const eocd = Buffer.from([0x50, 0x4b, 0x05, 0x06]);
  const eocdIndex = bytes.lastIndexOf(eocd);
  if (eocdIndex < 0 || eocdIndex + 22 > bytes.length) throw new Error(`${label}-pptx-eocd-invalid`);
  const entryCount = bytes.readUInt16LE(eocdIndex + 10);
  let cursor = bytes.readUInt32LE(eocdIndex + 16);
  const slides = new Set();
  for (let ordinal = 0; ordinal < entryCount; ordinal += 1) {
    if (cursor + 46 > bytes.length || bytes.readUInt32LE(cursor) !== 0x02014b50) {
      throw new Error(`${label}-pptx-central-directory-invalid`);
    }
    const nameLength = bytes.readUInt16LE(cursor + 28);
    const extraLength = bytes.readUInt16LE(cursor + 30);
    const commentLength = bytes.readUInt16LE(cursor + 32);
    const name = bytes.toString("utf8", cursor + 46, cursor + 46 + nameLength);
    const match = name.match(/^ppt\/slides\/slide([1-9][0-9]*)\.xml$/);
    if (match) slides.add(Number(match[1]));
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  const ordered = [...slides].sort((a, b) => a - b);
  if (ordered.some((value, index) => value !== index + 1)) throw new Error(`${label}-pptx-slide-sequence-invalid`);
  return ordered.length;
}

export function verifySignedReceipt(receipt, trust, expected) {
  if (trust.schemaVersion !== "authored-editable-hybrid-verifier-trust/v1" || trust.revoked !== false || trust.keyPurpose !== "editable-hybrid-mvp-gate1e" || !["local-shadow", "external-independent"].includes(trust.authorityScope) || trust.promotionAuthorized !== false || trust.deploymentAuthorized !== false) {
    throw new Error("gate1e-verifier-trust-invalid");
  }
  if (receipt.schemaVersion !== "authored-editable-hybrid-validation-receipt/v1" || receipt.issuerId !== trust.issuerId || receipt.signerKeyId !== trust.signerKeyId || receipt.authorityScope !== trust.authorityScope || receipt.promotionAuthorized !== false || receipt.deploymentAuthorized !== false) {
    throw new Error("gate1e-receipt-issuer-invalid");
  }
  for (const [key, value] of Object.entries(expected)) {
    if (receipt[key] !== value) throw new Error(`gate1e-receipt-subject-mismatch:${key}`);
  }
  const digest = digestExcluding(receipt, ["receiptDigest", "signatureBase64"]);
  if (receipt.receiptDigest !== digest) throw new Error("gate1e-receipt-digest-invalid");
  const publicKey = crypto.createPublicKey({
    key: Buffer.from(trust.publicKeySpkiBase64 || "", "base64"),
    type: "spki",
    format: "der",
  });
  if (!crypto.verify(null, Buffer.from(digest, "ascii"), publicKey, Buffer.from(receipt.signatureBase64 || "", "base64"))) {
    throw new Error("gate1e-receipt-signature-invalid");
  }
  return true;
}
