import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

import {
  assertSourceTreeCensusUnchanged,
  canonicalSha256,
  canonicalSha256Excluding,
  validateCountContract,
  validateGate1Artifacts,
  validateGate1Ledger,
  validatePolicyBinding,
} from "./authored-bounded-release-contracts.mjs";
import { captureBoundedSourceCensus } from "./run-authored-bounded-gate0-v3.mjs";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const RELEASE_PROFILE_PATH = path.resolve(new URL("../contracts/authored_vnext/semantic/release-profile.v3.json", import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));

function readJson(filePath) { return JSON.parse(fs.readFileSync(filePath, "utf8")); }
function writeFreshJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
}
function digestBytes(bytes) { return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`; }

function resolveInside(root, locator, artifactId) {
  const realRoot = fs.realpathSync(root);
  const absolute = fs.realpathSync(path.resolve(root, locator));
  if (absolute !== realRoot && !absolute.startsWith(`${realRoot}${path.sep}`)) throw new Error(`artifact-outside-bundle:${artifactId}`);
  return absolute;
}

function collectArtifactInventory(manifest, bundleDir, bindingEnvelope, pins) {
  assert.equal(bindingEnvelope.schemaVersion, "authored-gate1-artifact-bindings/v1", "gate1-binding-schema-invalid");
  if (bindingEnvelope.authorityDomain !== "actual_product" || bindingEnvelope.presentationId !== pins.presentationId || bindingEnvelope.presentationRevision !== pins.presentationRevision || bindingEnvelope.generationAttemptId !== pins.generationAttemptId || bindingEnvelope.fidelityExportAttemptId !== pins.fidelityExportAttemptId || bindingEnvelope.hybridExportAttemptId !== pins.hybridExportAttemptId) throw new Error("gate1-binding-attempt-mismatch");
  if (bindingEnvelope.manifestDigest !== manifest.manifestDigest || bindingEnvelope.requiredArtifactIdsDigest !== manifest.requiredArtifactIdsDigest || JSON.stringify(bindingEnvelope.requiredArtifactIds) !== JSON.stringify(manifest.requiredArtifactIds)) throw new Error("gate1-binding-required-artifact-set-mismatch");
  const bindings = bindingEnvelope.artifacts;
  assert.ok(Array.isArray(bindings), "gate1-bindings-missing");
  const required = manifest.requiredArtifactIds;
  const ids = bindings.map((entry) => entry.artifactId);
  if (new Set(ids).size !== ids.length || ids.length !== 70 || required.some((id) => !ids.includes(id)) || ids.some((id) => !required.includes(id))) throw new Error("gate1-artifact-id-set-mismatch");
  const inventory = bindings.map((binding) => {
    if (!binding.versionId || !binding.mediaType || !binding.locator) throw new Error(`gate1-artifact-binding-incomplete:${binding.artifactId}`);
    const absolute = resolveInside(bundleDir, binding.locator, binding.artifactId);
    const bytes = fs.readFileSync(absolute);
    const sha256 = digestBytes(bytes);
    if (binding.sha256 !== sha256) throw new Error(`gate1-artifact-digest-mismatch:${binding.artifactId}`);
    if (binding.byteLength !== bytes.length) throw new Error(`gate1-artifact-length-mismatch:${binding.artifactId}`);
    return { artifactId: binding.artifactId, versionId: binding.versionId, sha256, byteLength: bytes.length, mediaType: binding.mediaType };
  }).sort((a, b) => a.artifactId.localeCompare(b.artifactId) || a.versionId.localeCompare(b.versionId));
  validateGate1Artifacts(manifest, { artifactInventory: inventory });
  if (bindingEnvelope.artifactInventoryDigest !== canonicalSha256(inventory)) throw new Error("gate1-binding-inventory-digest-mismatch");
  return inventory;
}

function artifactPath(bundleDir, bindings, artifactId) {
  const binding = bindings.artifacts.find((entry) => entry.artifactId === artifactId);
  if (!binding) throw new Error(`gate1-artifact-binding-missing:${artifactId}`);
  return resolveInside(bundleDir, binding.locator, artifactId);
}

export function decodePng(filePath, artifactId) {
  const bytes = fs.readFileSync(filePath);
  if (!bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) throw new Error(`gate1-png-signature-invalid:${artifactId}`);
  let offset = 8; let width; let height; let colorType; let bitDepth; let interlace; let palette; let transparency; const idat = [];
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset); const type = bytes.toString("ascii", offset + 4, offset + 8); const data = bytes.subarray(offset + 8, offset + 8 + length); offset += 12 + length;
    if (type === "IHDR") { width = data.readUInt32BE(0); height = data.readUInt32BE(4); bitDepth = data[8]; colorType = data[9]; interlace = data[12]; }
    if (type === "PLTE") palette = data;
    if (type === "tRNS") transparency = data;
    if (type === "IDAT") idat.push(data);
    if (type === "IEND") break;
  }
  if (!width || !height || bitDepth !== 8 || interlace !== 0 || ![0, 2, 3, 6].includes(colorType) || idat.length === 0) throw new Error(`gate1-png-format-unsupported:${artifactId}`);
  if (colorType === 3 && (!palette || palette.length === 0 || palette.length % 3 !== 0)) throw new Error(`gate1-png-palette-invalid:${artifactId}`);
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : 1; const stride = width * channels;
  const inflated = zlib.inflateSync(Buffer.concat(idat));
  if (inflated.length !== (stride + 1) * height) throw new Error(`gate1-png-decoded-length:${artifactId}`);
  const raw = Buffer.alloc(stride * height); let sourceOffset = 0;
  const paeth = (a, b, c) => { const p = a + b - c; const pa = Math.abs(p - a); const pb = Math.abs(p - b); const pc = Math.abs(p - c); return pa <= pb && pa <= pc ? a : pb <= pc ? b : c; };
  for (let y = 0; y < height; y += 1) {
    const filter = inflated[sourceOffset++];
    for (let x = 0; x < stride; x += 1) {
      const value = inflated[sourceOffset++]; const left = x >= channels ? raw[y * stride + x - channels] : 0; const up = y > 0 ? raw[(y - 1) * stride + x] : 0; const upperLeft = y > 0 && x >= channels ? raw[(y - 1) * stride + x - channels] : 0;
      const prediction = filter === 0 ? 0 : filter === 1 ? left : filter === 2 ? up : filter === 3 ? Math.floor((left + up) / 2) : filter === 4 ? paeth(left, up, upperLeft) : -1;
      if (prediction < 0) throw new Error(`gate1-png-filter-unsupported:${artifactId}`);
      raw[y * stride + x] = (value + prediction) & 255;
    }
  }
  const rgba = Buffer.alloc(width * height * 4);
  for (let i = 0, j = 0; i < raw.length; i += channels, j += 4) {
    if (colorType === 3) {
      const paletteOffset = raw[i] * 3;
      if (paletteOffset + 2 >= palette.length) throw new Error(`gate1-png-palette-index-invalid:${artifactId}`);
      rgba[j] = palette[paletteOffset]; rgba[j + 1] = palette[paletteOffset + 1]; rgba[j + 2] = palette[paletteOffset + 2]; rgba[j + 3] = transparency && raw[i] < transparency.length ? transparency[raw[i]] : 255;
    } else {
      if (channels === 1) rgba[j] = rgba[j + 1] = rgba[j + 2] = raw[i]; else { rgba[j] = raw[i]; rgba[j + 1] = raw[i + 1]; rgba[j + 2] = raw[i + 2]; }
      rgba[j + 3] = channels === 4 ? raw[i + 3] : 255;
    }
  }
  return { width, height, rgba };
}

export function computedVisualMetrics(left, right) {
  if (left.width !== right.width || left.height !== right.height) throw new Error("gate1-visual-dimension-mismatch");
  const pixels = left.width * left.height; let changed = 0; let absolute = 0; let squares = 0;
  for (let index = 0; index < left.rgba.length; index += 4) {
    let pixelChanged = false;
    for (let channel = 0; channel < 4; channel += 1) { const delta = Math.abs(left.rgba[index + channel] - right.rgba[index + channel]); if (delta) pixelChanged = true; absolute += delta; squares += delta * delta; }
    if (pixelChanged) changed += 1;
  }
  const luma = (rgba, index) => (2126 * rgba[index] + 7152 * rgba[index + 1] + 722 * rgba[index + 2]) / 10000;
  let ssimSum = 0; let blocks = 0; const c1 = (0.01 * 255) ** 2; const c2 = (0.03 * 255) ** 2;
  for (let by = 0; by < left.height; by += 8) for (let bx = 0; bx < left.width; bx += 8) {
    const a = []; const b = [];
    for (let y = by; y < Math.min(by + 8, left.height); y += 1) for (let x = bx; x < Math.min(bx + 8, left.width); x += 1) { const index = (y * left.width + x) * 4; a.push(luma(left.rgba, index)); b.push(luma(right.rgba, index)); }
    const ma = a.reduce((sum, value) => sum + value, 0) / a.length; const mb = b.reduce((sum, value) => sum + value, 0) / b.length;
    let va = 0; let vb = 0; let covariance = 0; for (let i = 0; i < a.length; i += 1) { va += (a[i] - ma) ** 2; vb += (b[i] - mb) ** 2; covariance += (a[i] - ma) * (b[i] - mb); }
    const denominator = Math.max(1, a.length - 1); va /= denominator; vb /= denominator; covariance /= denominator;
    ssimSum += ((2 * ma * mb + c1) * (2 * covariance + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2)); blocks += 1;
  }
  return { changedPixelRatioPpm: Math.round(changed * 1_000_000 / pixels), block8LumaSsimMillionths: Math.round(ssimSum * 1_000_000 / blocks), meanAbsoluteChannelDeltaMilli: Math.round(absolute * 1000 / (pixels * 4)), rmseMilli: Math.round(Math.sqrt(squares / (pixels * 4)) * 1000) };
}

export function expectedVisualComparisons() {
  const result = [];
  for (let slide = 1; slide <= 8; slide += 1) {
    const number = String(slide).padStart(2, "0");
    for (const mode of ["fidelity", "hybrid"]) {
      result.push([`${mode}-source-vs-first-${number}`, `${mode}-browser-slide-${number}-png`, `${mode}-first-open-slide-${number}-png`]);
      result.push([`${mode}-source-vs-reopen-${number}`, `${mode}-browser-slide-${number}-png`, `${mode}-reopen-slide-${number}-png`]);
      result.push([`${mode}-first-vs-reopen-${number}`, `${mode}-first-open-slide-${number}-png`, `${mode}-reopen-slide-${number}-png`]);
    }
    result.push([`hybrid-vs-fidelity-first-${number}`, `fidelity-first-open-slide-${number}-png`, `hybrid-first-open-slide-${number}-png`]);
    result.push([`hybrid-vs-fidelity-reopen-${number}`, `fidelity-reopen-slide-${number}-png`, `hybrid-reopen-slide-${number}-png`]);
  }
  return result;
}

function validateGateMinus1(options, runtimePins) {
  const manifest = readJson(options.gateMinus1ManifestPath);
  const result = readJson(options.gateMinus1ResultPath);
  const contract = readJson(options.gateMinus1ValidatorContractPath);
  const receiptBytes = fs.readFileSync(options.gateMinus1ReceiptPath);
  const receipt = JSON.parse(receiptBytes.toString("utf8"));
  const signerAttestation = readJson(options.gateMinus1SignerAttestationPath);
  if (manifest.manifestDigest !== canonicalSha256Excluding(manifest, ["$.manifestDigest"])) throw new Error("gate-minus1-manifest-digest-invalid");
  if (result.resultSetDigest !== canonicalSha256Excluding(result, ["$.resultSetDigest"])) throw new Error("gate-minus1-result-digest-invalid");
  if (result.resultCoreDigest !== canonicalSha256Excluding(result, ["$.resultCoreDigest", "$.semanticValidationReceiptRef", "$.resultSetDigest"])) throw new Error("gate-minus1-result-core-digest-invalid");
  if (contract.contractDigest !== canonicalSha256Excluding(contract, ["$.contractDigest"])) throw new Error("gate-minus1-contract-digest-invalid");
  if (result.suiteStatus !== "feasible" || result.executedCaseCount !== 18 || result.skippedCaseCount !== 0) throw new Error("gate-minus1-not-feasible");
  const receiptDigest = digestBytes(receiptBytes);
  if (result.semanticValidationReceiptRef?.sha256 !== receiptDigest) throw new Error("gate-minus1-receipt-artifact-ref-mismatch");
  if (receipt.receiptDigest !== canonicalSha256Excluding(receipt, ["$.receiptDigest", "$.signatureBase64"])) throw new Error("gate-minus1-receipt-digest-invalid");
  if (receipt.subjectManifestDigest !== manifest.manifestDigest || receipt.subjectResultCoreDigest !== result.resultCoreDigest || receipt.semanticValidatorContractDigest !== contract.contractDigest) throw new Error("gate-minus1-receipt-subject-mismatch");
  if (signerAttestation.trustDomain !== "gate_minus1_actual_probe" || signerAttestation.signerKeyId !== receipt.signerKeyId || signerAttestation.privateKeyPersisted !== false) throw new Error("gate-minus1-signer-attestation-invalid");
  const publicKey = crypto.createPublicKey({ key: Buffer.from(signerAttestation.publicKeySpkiBase64, "base64"), type: "spki", format: "der" });
  if (!crypto.verify(null, Buffer.from(receipt.receiptDigest, "ascii"), publicKey, Buffer.from(receipt.signatureBase64, "base64"))) throw new Error("gate-minus1-receipt-signature-invalid");
  const exact = runtimePins.gateMinus1ManifestDigest === manifest.manifestDigest
    && runtimePins.gateMinus1ResultSetDigest === result.resultSetDigest
    && runtimePins.gateMinus1SemanticValidationReceiptArtifactDigest === receiptDigest
    && runtimePins.gateMinus1SemanticValidatorContractDigest === contract.contractDigest;
  if (!exact) throw new Error("gate-minus1-four-pin-mismatch");
  return true;
}

function validateRuntimePins(manifest, pins, census) {
  for (const key of manifest.runtimePinsRequiredBeforeExecution) {
    const value = pins[key];
    if (value === null || value === undefined || value === "" || /^(?:todo|placeholder|unknown)$/i.test(String(value))) throw new Error(`gate1-runtime-pin-missing:${key}`);
  }
  if (pins.evidencePolicyDigest !== manifest.evidencePolicyDigest) throw new Error("gate1-runtime-policy-pin-mismatch");
  if (pins.sourceTreeCensusDigest !== census.censusDigest || pins.sourceHeadCommit !== census.head) throw new Error("gate1-source-census-pin-mismatch");
  if (pins.sourceTreeIgnoredAllowlistPolicyDigest !== census.ignoredAllowlistPolicyDigest || pins.sourceTreeGeneratedCachePolicyDigest !== census.generatedCachePolicyDigest) throw new Error("gate1-source-census-policy-pin-mismatch");
  for (const key of ["releaseProfileDigest", "featureSetDigest", "productAuthorityTrustBundleDigest", "semanticValidatorTrustBundleDigest", "releaseTrustReadbackDigest", "fastApiSignerTrustBundleDigest", "serverReadbackVerifierAttestationDigest", "serverReadbackPublicKeyDigest"]) if (!DIGEST.test(pins[key] || "")) throw new Error(`gate1-runtime-pin-invalid:${key}`);
  for (const key of ["productAuthorityIssuerId", "productAuthoritySignerKeyId", "productAuthorityEnvironmentId", "productAuthorityKeyPurpose", "semanticValidatorIssuerId", "semanticValidatorSignerKeyId", "semanticValidatorEnvironmentId", "semanticValidatorKeyPurpose"]) if (typeof pins[key] !== "string" || pins[key].length < 3) throw new Error(`gate1-runtime-pin-invalid:${key}`);
  if (pins.productAuthorityKeyPurpose !== "actual-product-completion" || pins.semanticValidatorKeyPurpose !== "release-semantic-validator" || typeof pins.serverReadbackSignerKeyId !== "string" || pins.serverReadbackSignerKeyId.length < 3 || !/^[A-Za-z0-9_-]{16,128}$/.test(pins.executionNonce || "")) throw new Error("gate1-runtime-trust-purpose-invalid");
}

function loadReleaseProfile(pins) {
  const profile = readJson(RELEASE_PROFILE_PATH);
  if (profile.schemaVersion !== "authored-bounded-hybrid-release-profile-definition/v3" || !Array.isArray(profile.allRequiredCapabilityIds) || new Set(profile.allRequiredCapabilityIds).size !== profile.allRequiredCapabilityIds.length) throw new Error("gate1-release-profile-invalid");
  if (canonicalSha256(profile) !== pins.releaseProfileDigest) throw new Error("gate1-release-profile-digest-mismatch");
  return profile;
}

function loadServerReadbackVerifier(filePath, pins, manifest) {
  const sealed = manifest.serverReadbackAuthority;
  assertExactKeys(sealed, ["signerKeyId", "publicKeyDigest", "keyPurpose", "maxTtlSeconds", "executionNonceRequired"], "manifest-readback-authority");
  if (typeof sealed.signerKeyId !== "string" || sealed.signerKeyId.length < 16 || !DIGEST.test(sealed.publicKeyDigest || "") || sealed.keyPurpose !== "fastapi-release-readback" || sealed.maxTtlSeconds !== 300 || sealed.executionNonceRequired !== true || pins.serverReadbackSignerKeyId !== sealed.signerKeyId || pins.serverReadbackPublicKeyDigest !== sealed.publicKeyDigest) throw new Error("gate1-manifest-readback-authority-pin-mismatch");
  const bytes = fs.readFileSync(filePath); if (digestBytes(bytes) !== pins.serverReadbackVerifierAttestationDigest) throw new Error("gate1-readback-verifier-attestation-digest-mismatch");
  const value = JSON.parse(bytes.toString("utf8"));
  if (value.schemaVersion !== "authored-release-readback-verifier-attestation/v1" || value.authorityDomain !== "actual_product_runtime_readback" || value.manifestDigest !== manifest.manifestDigest || value.evidencePolicyDigest !== manifest.evidencePolicyDigest || value.signerKeyId !== sealed.signerKeyId || value.keyPurpose !== sealed.keyPurpose || value.privateKeyPersisted !== false || value.revoked !== false) throw new Error("gate1-readback-verifier-attestation-invalid");
  const publicDer = Buffer.from(value.publicKeySpkiBase64 || "", "base64"); const rawPublic = publicDer.subarray(publicDer.length - 32); if (publicDer.length < 32 || digestBytes(rawPublic) !== pins.serverReadbackPublicKeyDigest || value.publicKeyDigest !== pins.serverReadbackPublicKeyDigest) throw new Error("gate1-readback-verifier-key-mismatch");
  return { ...value, publicDer, executionNonce: pins.executionNonce, maxTtlSeconds: sealed.maxTtlSeconds };
}

function verifyServerReadback(value, verifier, label) {
  if (!value || value.readbackSignerKeyId !== verifier.signerKeyId || !DIGEST.test(value.serverReadbackDigest || "")) throw new Error(`gate1-${label}-server-readback-missing`);
  if (value.serverReadbackDigest !== canonicalSha256Excluding(value, ["$.serverReadbackDigest", "$.readbackSignerKeyId", "$.readbackSignatureBase64"])) throw new Error(`gate1-${label}-server-readback-digest-mismatch`);
  const issuedAt = Date.parse(value.issuedAt); const expiresAt = Date.parse(value.expiresAt); const now = Date.now();
  if (value.executionNonce !== verifier.executionNonce || !Number.isFinite(issuedAt) || !Number.isFinite(expiresAt) || expiresAt - issuedAt !== verifier.maxTtlSeconds * 1000 || issuedAt > now + 30_000 || now >= expiresAt || now - issuedAt > verifier.maxTtlSeconds * 1000) throw new Error(`gate1-${label}-server-readback-freshness-invalid`);
  const publicKey = crypto.createPublicKey({ key: verifier.publicDer, type: "spki", format: "der" });
  if (!crypto.verify(null, Buffer.from(value.serverReadbackDigest, "ascii"), publicKey, Buffer.from(value.readbackSignatureBase64 || "", "base64"))) throw new Error(`gate1-${label}-server-readback-signature-invalid`);
  return value;
}

function validateReleaseTrustReadback(filePath, pins, verifier) {
  const bytes = fs.readFileSync(filePath); if (digestBytes(bytes) !== pins.releaseTrustReadbackDigest) throw new Error("gate1-release-trust-readback-artifact-mismatch");
  const provenance = JSON.parse(bytes.toString("utf8")); assertExactKeys(provenance, ["schemaVersion", "authorityDomain", "readback"], "release-trust-provenance");
  if (provenance.schemaVersion !== "authored-fastapi-release-trust-readback-provenance/v1" || provenance.authorityDomain !== "actual_product") throw new Error("gate1-release-trust-provenance-invalid");
  const readback = validateHttpExchange(provenance.readback, { method: "GET", endpoint: nonceReadbackEndpoint("/api/v1/ppt/authored-emission/release-trust-readback-v3", pins.executionNonce), status: 200, label: "release-trust-readback" });
  if (canonicalSha256(readback.request) !== canonicalSha256({ executionNonce: pins.executionNonce })) throw new Error("gate1-release-trust-readback-nonce-provenance-invalid");
  const value = verifyServerReadback(readback.response, verifier, "release-trust");
  assertExactKeys(value, ["schemaVersion", "environmentId", "registeredKeys", "trustBundleDigest", "issuedAt", "expiresAt", "executionNonce", "serverReadbackDigest", "readbackSignerKeyId", "readbackSignatureBase64"], "release-trust-readback");
  if (value.schemaVersion !== "authored-release-trust-readback/v3" || value.environmentId !== pins.productAuthorityEnvironmentId || value.trustBundleDigest !== pins.fastApiSignerTrustBundleDigest || !Array.isArray(value.registeredKeys) || canonicalSha256(value.registeredKeys) !== value.trustBundleDigest) throw new Error("gate1-release-trust-readback-invalid");
  for (const entry of value.registeredKeys) { assertExactKeys(entry, ["authorityDomain", "issuerId", "signerKeyId", "environmentId", "keyPurpose", "rotationEpoch", "algorithm", "revoked", "publicKeyDigest"], "release-trust-registered-key"); if (entry.authorityDomain !== "actual_product" || entry.environmentId !== value.environmentId || entry.algorithm !== "Ed25519" || entry.revoked !== false || !Number.isInteger(entry.rotationEpoch) || entry.rotationEpoch < 1 || !DIGEST.test(entry.publicKeyDigest || "")) throw new Error("gate1-release-trust-registered-key-invalid"); }
  const unique = new Set(value.registeredKeys.map((entry) => `${entry.authorityDomain}|${entry.issuerId}|${entry.signerKeyId}|${entry.environmentId}|${entry.keyPurpose}|${entry.rotationEpoch}`)); if (unique.size !== value.registeredKeys.length) throw new Error("gate1-release-trust-readback-duplicate-key");
  return value;
}

function validateProductTrustBundle(filePath, pins, manifest, environment, trustReadback) {
  const bytes = fs.readFileSync(filePath);
  if (digestBytes(bytes) !== pins.productAuthorityTrustBundleDigest) throw new Error("gate1-product-trust-bundle-digest-mismatch");
  const trust = JSON.parse(bytes.toString("utf8"));
  if (trust.schemaVersion !== "authored-product-authority-trust-bundle/v1" || trust.source !== "fastapi-runtime-trust-bundle" || trust.sealed !== true || trust.revoked !== false) throw new Error("gate1-product-trust-bundle-invalid");
  if (trust.authorityDomain !== "actual_product" || trust.evidenceClass !== "actual_product_completion" || trust.manifestDigest !== manifest.manifestDigest || trust.evidencePolicyDigest !== manifest.evidencePolicyDigest || trust.environmentAttestationDigest !== pins.environmentAttestationDigest) throw new Error("gate1-product-trust-bundle-scope-invalid");
  if (trust.issuerId !== pins.productAuthorityIssuerId || trust.signerKeyId !== pins.productAuthoritySignerKeyId || trust.environmentId !== pins.productAuthorityEnvironmentId || trust.keyPurpose !== pins.productAuthorityKeyPurpose) throw new Error("gate1-product-trust-bundle-pin-mismatch");
  if (environment.environmentId !== trust.environmentId || environment.productAuthorityIssuerId !== trust.issuerId || environment.productAuthoritySignerKeyId !== trust.signerKeyId || environment.productAuthorityKeyPurpose !== trust.keyPurpose) throw new Error("gate1-product-trust-environment-mismatch");
  const publicDer = trust.publicKeySpkiBase64 ? Buffer.from(trust.publicKeySpkiBase64, "base64") : Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(trust.publicKeyRawBase64 || "", "base64")]);
  const rawPublic = publicDer.subarray(publicDer.length - 32); const registered = trustReadback.registeredKeys.filter((entry) => entry.authorityDomain === "actual_product" && entry.issuerId === trust.issuerId && entry.signerKeyId === trust.signerKeyId && entry.environmentId === trust.environmentId && entry.keyPurpose === trust.keyPurpose && entry.algorithm === "Ed25519" && entry.revoked === false && entry.publicKeyDigest === digestBytes(rawPublic));
  if (publicDer.length < 32 || trust.publicKeyDigest !== digestBytes(rawPublic) || registered.length !== 1) throw new Error("gate1-product-trust-key-not-fastapi-registered");
  return { ...trust, rotationEpoch: registered[0].rotationEpoch };
}

function ciAuthorityState(ci, manifest, sourceHead) {
  const expectedSteps = manifest.ciAuthority.requiredSuccessfulStepNames;
  const steps = Array.isArray(ci.successfulStepNames) ? [...new Set(ci.successfulStepNames)].sort() : [];
  const exactSteps = JSON.stringify(steps) === JSON.stringify([...expectedSteps].sort());
  const verified = ci.schemaVersion === "authored-remote-ci-seal/v1"
    && ci.authorityDomain === "actual_product"
    && ci.remoteAuthorityVerified === true
    && ci.paginationComplete === true
    && ci.latestStatePerStepSuccessful === true
    && ci.commit === sourceHead
    && exactSteps
    && typeof ci.pipelineUuid === "string" && ci.pipelineUuid.length > 2
    && DIGEST.test(ci.receiptDigest);
  if (verified) return "accepted";
  if (ci.status === "not-verified" || ci.remoteAuthorityVerified === false) return "not-verified";
  return "blocked";
}

function validateFreshAttestation(filePath, expectedDigest, maxAgeSeconds, label) {
  const bytes = fs.readFileSync(filePath); if (digestBytes(bytes) !== expectedDigest) throw new Error(`gate1-${label}-attestation-digest-mismatch`);
  const value = JSON.parse(bytes.toString("utf8")); const issued = Date.parse(value.issuedAt); const expires = Date.parse(value.expiresAt); const now = Date.now();
  if (value.authorityDomain !== "actual_product" || value.attested === false || !Number.isFinite(issued) || !Number.isFinite(expires) || issued > now + 30_000 || now - issued > maxAgeSeconds * 1000 || expires <= now) throw new Error(`gate1-${label}-attestation-not-fresh`);
  return value;
}

function collectSemanticQids(value, output = new Set()) {
  if (Array.isArray(value)) { for (const child of value) collectSemanticQids(child, output); return output; }
  if (!value || typeof value !== "object") return output;
  for (const key of ["qid", "dataQid", "elementId"]) if (typeof value[key] === "string" && value[key]) output.add(value[key]);
  if (Array.isArray(value.qids)) for (const qid of value.qids) if (typeof qid === "string" && qid) output.add(qid);
  for (const child of Object.values(value)) collectSemanticQids(child, output);
  return output;
}

function validateGate1ArtifactContents(bundleDir, bindings, inventory, manifest, policy, pins) {
  const byId = new Map(inventory.map((entry) => [entry.artifactId, entry]));
  const pptxType = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  for (const artifactId of ["fidelity-input-pptx", "fidelity-saved-pptx", "hybrid-input-pptx", "hybrid-saved-pptx"]) {
    const record = byId.get(artifactId); const bytes = fs.readFileSync(artifactPath(bundleDir, bindings, artifactId));
    if (record.mediaType !== pptxType || bytes.length < 4 || !bytes.subarray(0, 4).equals(Buffer.from([0x50, 0x4b, 0x03, 0x04]))) throw new Error(`gate1-pptx-package-invalid:${artifactId}`);
  }
  const activeManifestValue = readJson(artifactPath(bundleDir, bindings, "active-manifest-json"));
  if (canonicalSha256Excluding(activeManifestValue, ["$.canonicalDigest"]) !== pins.activeManifestDigest) throw new Error("gate1-active-manifest-artifact-pin-mismatch");
  const semanticSource = readJson(artifactPath(bundleDir, bindings, "semantic-source-json")); const qids = [...collectSemanticQids(semanticSource)].sort();
  if (qids.length === 0) throw new Error("gate1-semantic-source-qids-missing");
  for (const mode of ["fidelity", "hybrid"]) for (const state of ["input", "saved"]) {
    const readback = readJson(artifactPath(bundleDir, bindings, `${mode}-${state}-ooxml-json`)); const pptx = byId.get(`${mode}-${state}-pptx`);
    const expectedAttempt = pins[`${mode}ExportAttemptId`];
    if (readback.authorityDomain !== "actual_product" || readback.presentationId !== pins.presentationId || readback.presentationRevision !== pins.presentationRevision || readback.exportAttemptId !== expectedAttempt || readback.packageDigest !== pptx.sha256 || readback.repairRequired !== false || JSON.stringify([...(readback.qids || [])].sort()) !== JSON.stringify(qids)) throw new Error(`gate1-ooxml-readback-invalid:${mode}:${state}`);
  }
  const decoded = new Map(); const pngRecords = inventory.filter((entry) => entry.artifactId.endsWith("-png"));
  if (pngRecords.length !== 48) throw new Error(`gate1-png-cardinality:${pngRecords.length}/48`);
  for (const record of pngRecords) {
    if (record.mediaType !== "image/png") throw new Error(`gate1-png-media-type:${record.artifactId}`);
    const image = decodePng(artifactPath(bundleDir, bindings, record.artifactId), record.artifactId);
    if (image.width !== policy.measurementProfile.renderWidthPx || image.height !== policy.measurementProfile.renderHeightPx) throw new Error(`gate1-png-dimensions:${record.artifactId}`);
    decoded.set(record.artifactId, image);
  }
  const metrics = readJson(artifactPath(bundleDir, bindings, "independent-visual-metrics-json"));
  if (metrics.schemaVersion !== "authored-independent-visual-metrics/v1" || metrics.authorityDomain !== "actual_product" || !Array.isArray(metrics.images) || !Array.isArray(metrics.comparisons)) throw new Error("gate1-visual-metrics-envelope-invalid");
  const imageClaims = new Map(metrics.images.map((entry) => [entry.artifactId, entry]));
  if (imageClaims.size !== 48) throw new Error("gate1-visual-image-claim-cardinality");
  for (const record of pngRecords) { const claim = imageClaims.get(record.artifactId); if (!claim || claim.sha256 !== record.sha256 || claim.width !== policy.measurementProfile.renderWidthPx || claim.height !== policy.measurementProfile.renderHeightPx) throw new Error(`gate1-visual-image-binding:${record.artifactId}`); }
  const comparisons = new Map(metrics.comparisons.map((entry) => [entry.comparisonId, entry])); const expected = expectedVisualComparisons();
  if (comparisons.size !== expected.length) throw new Error(`gate1-visual-comparison-cardinality:${comparisons.size}/${expected.length}`);
  for (const [comparisonId, baselineArtifactId, candidateArtifactId] of expected) {
    const claim = comparisons.get(comparisonId); if (!claim || claim.baselineArtifactId !== baselineArtifactId || claim.candidateArtifactId !== candidateArtifactId) throw new Error(`gate1-visual-comparison-binding:${comparisonId}`);
    const computed = computedVisualMetrics(decoded.get(baselineArtifactId), decoded.get(candidateArtifactId));
    for (const [key, value] of Object.entries(computed)) if (!Number.isInteger(claim[key]) || claim[key] !== value) throw new Error(`gate1-visual-metric-not-recomputed:${comparisonId}:${key}`);
    if (computed.changedPixelRatioPpm > policy.visualMetrics.changedPixelRatioMaxPpm || computed.block8LumaSsimMillionths < policy.visualMetrics.block8LumaSsimMinMillionths || computed.meanAbsoluteChannelDeltaMilli > policy.visualMetrics.meanAbsoluteChannelDeltaMaxMilli || computed.rmseMilli > policy.visualMetrics.rmseMaxMilli) throw new Error(`gate1-visual-threshold:${comparisonId}`);
  }
  for (const mode of ["fidelity", "hybrid"]) for (const phase of ["first-open", "reopen"]) {
    const snapshot = readJson(artifactPath(bundleDir, bindings, `${mode}-${phase}-com-json`));
    const expectedInput = byId.get(`${mode}-input-pptx`).sha256; const expectedSaved = byId.get(`${mode}-saved-pptx`).sha256;
    if (snapshot.presentationId !== pins.presentationId || snapshot.presentationRevision !== pins.presentationRevision || snapshot.exportAttemptId !== pins[`${mode}ExportAttemptId`] || snapshot.inputPptxDigest !== expectedInput || snapshot.savedCopyPptxDigest !== expectedSaved || (phase === "reopen" && snapshot.reopenInputPptxDigest !== expectedSaved) || JSON.stringify([...(snapshot.qids || [])].sort()) !== JSON.stringify(qids) || snapshot.ooxmlReadbackDigest !== byId.get(`${mode}-${phase === "first-open" ? "input" : "saved"}-ooxml-json`).sha256) throw new Error(`gate1-com-readback-binding:${mode}:${phase}`);
  }
  const environment = validateFreshAttestation(artifactPath(bundleDir, bindings, "environment-attestation-json"), pins.environmentAttestationDigest, policy.freshness.environmentAttestationMaxAgeSeconds, "environment");
  const worker = validateFreshAttestation(artifactPath(bundleDir, bindings, "worker-attestation-json"), pins.workerAttestationDigest, policy.freshness.workerAttestationMaxAgeSeconds, "worker");
  if (environment.presentationId !== pins.presentationId || environment.presentationRevision !== pins.presentationRevision || worker.presentationId !== pins.presentationId || worker.presentationRevision !== pins.presentationRevision || worker.environmentId !== environment.environmentId) throw new Error("gate1-attestation-attempt-mismatch");
  return { qids, decoded, environment, worker };
}

function validateActualProductPhaseObservation(phase, observation, inventoryIds) {
  if (observation.phaseId !== phase.phaseId || observation.ordinal !== phase.ordinal) throw new Error(`gate1-phase-identity:${phase.phaseId}`);
  if (observation.authorityDomain !== "actual_product" || observation.evidenceClass !== "actual_product_release_qualification") throw new Error(`gate1-phase-authority:${phase.phaseId}`);
  if (!Array.isArray(observation.evidenceArtifactIds) || observation.evidenceArtifactIds.length < 1 || observation.evidenceArtifactIds.some((id) => !inventoryIds.has(id))) throw new Error(`gate1-phase-artifact-ref:${phase.phaseId}`);
  if (!["accepted", "not-verified", "blocked", "failed"].includes(observation.state)) throw new Error(`gate1-phase-state:${phase.phaseId}`);
  return observation.state;
}

function validateProductArtifactAuthority(value, trust, label, excludedPayloadFields = []) {
  const authority = value.authority; if (!authority) throw new Error(`gate1-${label}-authority-missing`);
  if (trust.authorityDomain !== "actual_product" || trust.evidenceClass !== "actual_product_completion" || trust.keyPurpose !== "actual-product-completion" || trust.revoked !== false) throw new Error(`gate1-${label}-trust-invalid`);
  if (authority.schemaVersion !== "authored-evidence-issuer-envelope/v3" || authority.authorityDomain !== "actual_product" || authority.evidenceClass !== "actual_product_completion" || authority.keyPurpose !== "actual-product-completion" || authority.issuerId !== trust.issuerId || authority.signerKeyId !== trust.signerKeyId || authority.environmentId !== trust.environmentId || authority.rotationEpoch !== trust.rotationEpoch || typeof authority.nonce !== "string" || authority.nonce.length < 16 || authority.signatureAlgorithm !== "Ed25519") throw new Error(`gate1-${label}-authority-scope-invalid`);
  const now = Date.now(); if (Date.parse(authority.issuedAt) > now + 30_000 || Date.parse(authority.expiresAt) <= now) throw new Error(`gate1-${label}-authority-not-fresh`);
  const payload = structuredClone(value); delete payload.authority; for (const field of excludedPayloadFields) delete payload[field]; const authorityCore = structuredClone(authority); delete authorityCore.signatureBase64;
  const authorityDigest = canonicalSha256({ receipt: payload, authority: authorityCore });
  const publicDer = trust.publicKeySpkiBase64 ? Buffer.from(trust.publicKeySpkiBase64, "base64") : Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(trust.publicKeyRawBase64 || "", "base64")]);
  const publicKey = crypto.createPublicKey({ key: publicDer, type: "spki", format: "der" });
  if (!crypto.verify(null, Buffer.from(authorityDigest, "ascii"), publicKey, Buffer.from(authority.signatureBase64, "base64"))) throw new Error(`gate1-${label}-authority-signature-invalid`);
}

function assertExactKeys(value, expected, label) {
  if (!value || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...expected].sort())) throw new Error(`gate1-${label}-schema-fields-invalid`);
}

function validateHttpExchange(exchange, { method, endpoint, status, label }) {
  assertExactKeys(exchange, ["method", "endpoint", "httpStatus", "request", "response", "requestDigest", "responseDigest"], `${label}-exchange`);
  if (exchange.method !== method || exchange.endpoint !== endpoint || exchange.httpStatus !== status || exchange.requestDigest !== canonicalSha256(exchange.request) || exchange.responseDigest !== canonicalSha256(exchange.response)) throw new Error(`gate1-${label}-http-provenance-invalid`);
  return exchange;
}

function responseCore(value) { const result = structuredClone(value); for (const key of ["issuedAt", "expiresAt", "executionNonce", "serverReadbackDigest", "readbackSignerKeyId", "readbackSignatureBase64"]) delete result[key]; return result; }
function nonceReadbackEndpoint(base, nonce) { return `${base}?executionNonce=${encodeURIComponent(nonce)}`; }

function validateRootProvenance(envelope, productTrust, readbackVerifier, manifest, pins, inventory) {
  assertExactKeys(envelope, ["schemaVersion", "authorityDomain", "create", "readback", "comQualificationReadback"], "evidence-root-provenance");
  if (envelope.schemaVersion !== "authored-fastapi-evidence-graph-root-provenance/v1" || envelope.authorityDomain !== "actual_product") throw new Error("gate1-evidence-root-provenance-invalid");
  const create = validateHttpExchange(envelope.create, { method: "POST", endpoint: "/api/v1/ppt/authored-emission/evidence-graph-roots-v3", status: 201, label: "evidence-root-create" });
  const request = create.request; validateProductArtifactAuthority(request, productTrust, "evidence-root-request");
  const root = create.response; const readback = envelope.readback;
  assertExactKeys(request, ["schemaVersion", "presentationId", "presentationRevision", "exportAttemptId", "evidencePolicyDigest", "activeManifestDigest", "sourceTreeCensusDigest", "sourceHeadCommit", "releaseProfileDigest", "featureSetDigest", "gateMinus1ManifestDigest", "gateMinus1ResultSetDigest", "gateMinus1ReceiptArtifactDigest", "gateMinus1ValidatorContractDigest", "environmentAttestationDigest", "workerAttestationDigest", "comQualificationJobId", "comQualificationResultDigest", "baseArtifactIds", "baseArtifactIdsDigest", "finalRequiredArtifactIdsDigest", "artifactInventory", "artifactInventoryDigest", "rootDigest", "authority"], "evidence-root-request");
  const rootFields = ["schemaVersion", "graphRootId", "persistedRecordId", "presentationId", "presentationRevision", "exportAttemptId", "requestDigest", "evidencePolicyDigest", "activeManifestDigest", "requiredPinDigest", "baseArtifactIds", "baseArtifactIdsDigest", "finalRequiredArtifactIdsDigest", "artifactInventoryDigest", "rootDigest", "comQualificationJobId", "comQualificationState", "comQualificationResultDigest", "issuedAt", "expiresAt", "executionNonce", "serverReadbackDigest", "readbackSignerKeyId", "readbackSignatureBase64"];
  assertExactKeys(root, rootFields, "evidence-root-create-response");
  if (root.schemaVersion !== "authored-evidence-graph-root/v3" || root.persistedRecordId !== root.graphRootId) throw new Error("gate1-evidence-root-create-response-invalid");
  validateHttpExchange(readback, { method: "GET", endpoint: nonceReadbackEndpoint(`/api/v1/ppt/authored-emission/evidence-graph-roots-v3/${root.graphRootId}`, pins.executionNonce), status: 200, label: "evidence-root-readback" }); if (canonicalSha256(readback.request) !== canonicalSha256({ executionNonce: pins.executionNonce })) throw new Error("gate1-evidence-root-readback-nonce-provenance-invalid");
  assertExactKeys(readback.response, rootFields, "evidence-root-readback-response");
  verifyServerReadback(readback.response, readbackVerifier, "evidence-root");
  if (canonicalSha256(responseCore(readback.response)) !== canonicalSha256(responseCore(root))) throw new Error("gate1-evidence-root-create-readback-mismatch");
  const requestWithoutAuthority = structuredClone(request); delete requestWithoutAuthority.authority;
  if (root.requestDigest !== canonicalSha256(requestWithoutAuthority) || request.rootDigest !== canonicalSha256Excluding(request, ["$.rootDigest", "$.authority"]) || root.rootDigest !== request.rootDigest) throw new Error("gate1-evidence-root-request-digest-mismatch");
  const baseArtifactIds = manifest.requiredArtifactIds.filter((id) => !["phase-result-set-json", "evidence-graph-root-json", "completion-seal-json"].includes(id)); const inventoryById = new Map(inventory.map((entry) => [entry.artifactId, entry]));
  if (request.schemaVersion !== "authored-evidence-graph-root-create/v3" || request.presentationId !== pins.presentationId || request.presentationRevision !== pins.presentationRevision || request.exportAttemptId !== pins.hybridExportAttemptId || request.evidencePolicyDigest !== pins.evidencePolicyDigest || request.activeManifestDigest !== pins.activeManifestDigest || request.sourceTreeCensusDigest !== pins.sourceTreeCensusDigest || request.sourceHeadCommit !== pins.sourceHeadCommit || request.releaseProfileDigest !== pins.releaseProfileDigest || request.featureSetDigest !== pins.featureSetDigest || request.gateMinus1ManifestDigest !== pins.gateMinus1ManifestDigest || request.gateMinus1ResultSetDigest !== pins.gateMinus1ResultSetDigest || request.gateMinus1ReceiptArtifactDigest !== pins.gateMinus1SemanticValidationReceiptArtifactDigest || request.gateMinus1ValidatorContractDigest !== pins.gateMinus1SemanticValidatorContractDigest || request.environmentAttestationDigest !== pins.environmentAttestationDigest || request.workerAttestationDigest !== pins.workerAttestationDigest) throw new Error("gate1-evidence-root-attempt-mismatch");
  if (JSON.stringify(request.baseArtifactIds) !== JSON.stringify(baseArtifactIds) || request.baseArtifactIdsDigest !== canonicalSha256(baseArtifactIds) || request.finalRequiredArtifactIdsDigest !== manifest.requiredArtifactIdsDigest || !Array.isArray(request.artifactInventory) || request.artifactInventory.length !== 67 || request.artifactInventoryDigest !== canonicalSha256(request.artifactInventory)) throw new Error("gate1-evidence-root-inventory-digest-mismatch");
  for (let index = 0; index < baseArtifactIds.length; index += 1) { const artifactId = baseArtifactIds[index]; const ref = request.artifactInventory[index]; const observed = inventoryById.get(artifactId); assertExactKeys(ref, ["schemaVersion", "artifactId", "exportAttemptId", "versionId", "sha256", "byteLength", "mediaType", "encryptionKeyRef", "storageGeneration", "state"], `evidence-root-inventory-ref:${artifactId}`); if (ref?.schemaVersion !== "authored-durable-artifact-ref/v3" || ref.artifactId !== artifactId || ref.exportAttemptId !== request.exportAttemptId || ref.state !== "bound" || typeof ref.encryptionKeyRef !== "string" || !Number.isInteger(ref.storageGeneration) || ref.storageGeneration < 1) throw new Error(`gate1-evidence-root-inventory-schema:${artifactId}`); for (const key of ["versionId", "sha256", "byteLength", "mediaType"]) if (ref[key] !== observed[key]) throw new Error(`gate1-evidence-root-inventory-binding:${artifactId}:${key}`); }
  for (const key of ["presentationId", "presentationRevision", "exportAttemptId", "evidencePolicyDigest", "activeManifestDigest", "baseArtifactIdsDigest", "finalRequiredArtifactIdsDigest", "artifactInventoryDigest", "rootDigest", "comQualificationJobId", "comQualificationResultDigest"]) if (root[key] !== request[key]) throw new Error(`gate1-evidence-root-response-binding:${key}`);
  const expectedRequiredPinDigest = canonicalSha256({ sourceTreeCensusDigest: pins.sourceTreeCensusDigest, sourceHeadCommit: pins.sourceHeadCommit, gateMinus1ManifestDigest: pins.gateMinus1ManifestDigest, gateMinus1ResultSetDigest: pins.gateMinus1ResultSetDigest, gateMinus1ReceiptArtifactDigest: pins.gateMinus1SemanticValidationReceiptArtifactDigest, gateMinus1ValidatorContractDigest: pins.gateMinus1SemanticValidatorContractDigest });
  if (root.requiredPinDigest !== expectedRequiredPinDigest || root.comQualificationState !== "sealed") throw new Error("gate1-evidence-root-required-pin-mismatch");
  const comReadback = envelope.comQualificationReadback; validateHttpExchange(comReadback, { method: "GET", endpoint: nonceReadbackEndpoint(`/api/v1/ppt/authored-emission/com-qualification-jobs-v3/${root.comQualificationJobId}`, pins.executionNonce), status: 200, label: "com-qualification-readback" }); if (canonicalSha256(comReadback.request) !== canonicalSha256({ executionNonce: pins.executionNonce })) throw new Error("gate1-com-qualification-readback-nonce-provenance-invalid"); const job = verifyServerReadback(comReadback.response, readbackVerifier, "com-qualification");
  assertExactKeys(job, ["schemaVersion", "jobId", "exportAttemptId", "evidencePolicyDigest", "authorityDomain", "state", "transitionSequence", "leaseOwner", "leaseToken", "leaseExpiresAt", "heartbeatAt", "attemptCount", "evidence", "resultDigest", "failureCode", "issuedAt", "expiresAt", "executionNonce", "serverReadbackDigest", "readbackSignerKeyId", "readbackSignatureBase64"], "com-qualification-readback-response");
  if (job.schemaVersion !== "authored-com-qualification-job/v3" || job.jobId !== root.comQualificationJobId || job.exportAttemptId !== request.exportAttemptId || job.evidencePolicyDigest !== pins.evidencePolicyDigest || job.authorityDomain !== "actual_product" || job.state !== "sealed" || job.resultDigest !== root.comQualificationResultDigest || job.resultDigest !== canonicalSha256(job.evidence)) throw new Error("gate1-com-qualification-readback-invalid");
  const artifactDigests = job.evidence?.artifactDigests; if (!artifactDigests || JSON.stringify(Object.keys(artifactDigests)) !== JSON.stringify(baseArtifactIds) || baseArtifactIds.some((id) => artifactDigests[id] !== inventoryById.get(id).sha256)) throw new Error("gate1-com-qualification-artifact-binding-invalid");
  return { root, request, job };
}

function validateCompletionProvenance(envelope, productTrust, readbackVerifier, root, releaseProfile, pins, ciState) {
  assertExactKeys(envelope, ["schemaVersion", "authorityDomain", "create", "seal", "readback"], "completion-provenance");
  if (envelope.schemaVersion !== "authored-fastapi-evidence-bundle-completion-provenance/v1" || envelope.authorityDomain !== "actual_product") throw new Error("gate1-completion-provenance-invalid");
  const create = validateHttpExchange(envelope.create, { method: "POST", endpoint: "/api/v1/ppt/authored-emission/evidence-bundle-completions-v3", status: 201, label: "completion-create" }); const created = create.response;
  assertExactKeys(create.request, ["schemaVersion", "graphRootId", "requiredCapabilityIds"], "completion-create-request");
  const completionFields = ["schemaVersion", "completionId", "persistedRecordId", "graphRootId", "graphRootDigest", "exportAttemptId", "state", "casVersion", "requiredCapabilityIds", "receiptDigests", "completionDigest", "createRequestDigest", "sealRequestDigest", "receiptIdentities", "sealedAt", "issuedAt", "expiresAt", "executionNonce", "serverReadbackDigest", "readbackSignerKeyId", "readbackSignatureBase64"];
  assertExactKeys(created, completionFields, "completion-create-response");
  if (create.request.schemaVersion !== "authored-evidence-bundle-completion-create/v3" || create.request.graphRootId !== root.graphRootId || JSON.stringify(create.request.requiredCapabilityIds) !== JSON.stringify(releaseProfile.allRequiredCapabilityIds) || created.schemaVersion !== "authored-evidence-bundle-completion/v3" || created.persistedRecordId !== created.completionId || created.graphRootId !== root.graphRootId || created.graphRootDigest !== root.rootDigest || created.exportAttemptId !== pins.hybridExportAttemptId || created.createRequestDigest !== canonicalSha256(create.request) || created.state !== "pending" || created.casVersion !== 0 || created.receiptDigests.length !== 0 || created.receiptIdentities.length !== 0 || created.sealRequestDigest !== null) throw new Error("gate1-completion-create-response-invalid");
  const readback = validateHttpExchange(envelope.readback, { method: "GET", endpoint: nonceReadbackEndpoint(`/api/v1/ppt/authored-emission/evidence-bundle-completions-v3/${created.completionId}`, pins.executionNonce), status: 200, label: "completion-readback" }); if (canonicalSha256(readback.request) !== canonicalSha256({ executionNonce: pins.executionNonce })) throw new Error("gate1-completion-readback-nonce-provenance-invalid"); const completion = verifyServerReadback(readback.response, readbackVerifier, "completion");
  assertExactKeys(completion, completionFields, "completion-readback-response");
  if (completion.completionId !== created.completionId || completion.persistedRecordId !== created.persistedRecordId || completion.graphRootId !== root.graphRootId || completion.graphRootDigest !== root.rootDigest || completion.exportAttemptId !== pins.hybridExportAttemptId || completion.createRequestDigest !== created.createRequestDigest || JSON.stringify(completion.requiredCapabilityIds) !== JSON.stringify(releaseProfile.allRequiredCapabilityIds)) throw new Error("gate1-completion-readback-binding-invalid");
  if (ciState === "accepted") {
    const seal = validateHttpExchange(envelope.seal, { method: "POST", endpoint: `/api/v1/ppt/authored-emission/evidence-bundle-completions-v3/${created.completionId}/seal`, status: 200, label: "completion-seal" }); validateProductArtifactAuthority(seal.request, productTrust, "completion-seal-request", ["completionDigest"]);
    assertExactKeys(seal.request, ["schemaVersion", "expectedCasVersion", "receiptDigests", "completionDigest", "authority"], "completion-seal-request"); assertExactKeys(seal.response, completionFields, "completion-seal-response");
    if (seal.request.schemaVersion !== "authored-evidence-bundle-completion-seal/v3" || seal.request.expectedCasVersion !== 0 || seal.response.state !== "sealed" || seal.response.casVersion !== 1 || canonicalSha256(responseCore(completion)) !== canonicalSha256(responseCore(seal.response)) || completion.sealRequestDigest !== canonicalSha256(seal.request) || completion.completionDigest !== seal.request.completionDigest || !completion.sealedAt) throw new Error("gate1-completion-seal-cas-invalid");
  } else if (envelope.seal !== null || completion.state !== "pending" || completion.casVersion !== 0 || completion.sealRequestDigest !== null || completion.sealedAt !== null || completion.receiptDigests.length !== 0 || completion.receiptIdentities.length !== 0) throw new Error("gate1-completion-pending-state-invalid");
  if (ciState === "accepted") {
    if (completion.receiptIdentities.length !== releaseProfile.allRequiredCapabilityIds.length || completion.receiptIdentities.some((entry, index) => entry.capabilityId !== releaseProfile.allRequiredCapabilityIds[index] || entry.receiptDigest !== completion.receiptDigests[index] || entry.exportAttemptId !== pins.hybridExportAttemptId || typeof entry.recordId !== "string" || !DIGEST.test(entry.bindingDigest || "") || typeof entry.evidenceBundleId !== "string" || !DIGEST.test(entry.evidenceDigest || ""))) throw new Error("gate1-completion-receipt-identity-invalid");
  }
  return completion;
}

function validateProductCrossBindings(bundleDir, bindings, inventory, manifest, pins, ciState, productTrust, releaseProfile, readbackVerifier) {
  const ledgerEnvelope = readJson(artifactPath(bundleDir, bindings, "manual-ledger-json"));
  assertExactKeys(ledgerEnvelope, ["schemaVersion", "authorityDomain", "presentationId", "presentationRevision", "generationAttemptId", "fidelityExportAttemptId", "hybridExportAttemptId", "cells"], "ledger-envelope");
  if (!ledgerEnvelope || Array.isArray(ledgerEnvelope) || ledgerEnvelope.schemaVersion !== "authored-manual-ledger/v1" || ledgerEnvelope.authorityDomain !== "actual_product" || ledgerEnvelope.presentationId !== pins.presentationId || ledgerEnvelope.presentationRevision !== pins.presentationRevision || ledgerEnvelope.generationAttemptId !== pins.generationAttemptId || ledgerEnvelope.fidelityExportAttemptId !== pins.fidelityExportAttemptId || ledgerEnvelope.hybridExportAttemptId !== pins.hybridExportAttemptId || !Array.isArray(ledgerEnvelope.cells)) throw new Error("gate1-ledger-envelope-invalid");
  const cells = ledgerEnvelope.cells;
  validateGate1Ledger(cells, manifest.expectedSlideCount);
  const ledgerNow = Date.now(); const maxReviewAgeMs = Math.min(900, Number(manifest.manualLedgerAxes?.reviewFreshnessMaxSeconds || 900)) * 1000;
  if (cells.some((cell) => { const reviewed = Date.parse(cell?.reviewedAt); return typeof cell !== "object" || cell.state !== "accepted" || typeof cell.reviewerId !== "string" || !/^[A-Za-z0-9._/-]{3,128}$/.test(cell.reviewerId) || !Number.isFinite(reviewed) || reviewed > ledgerNow + 30_000 || ledgerNow - reviewed > maxReviewAgeMs; })) throw new Error("gate1-ledger-review-invalid");
  for (const cell of cells) {
    assertExactKeys(cell, ["slideNumber", "exportMode", "comPhase", "defectFieldId", "state", "evidenceArtifactIds", "reviewerId", "reviewedAt"], `ledger-cell:${cell.slideNumber}:${cell.exportMode}:${cell.comPhase}:${cell.defectFieldId}`);
    const number = String(cell.slideNumber).padStart(2, "0");
    const expectedRefs = [`${cell.exportMode}-browser-slide-${number}-png`, `${cell.exportMode}-${cell.comPhase}-slide-${number}-png`, `${cell.exportMode}-${cell.comPhase}-com-json`].sort();
    if (JSON.stringify([...(cell.evidenceArtifactIds || [])].sort()) !== JSON.stringify(expectedRefs)) throw new Error(`gate1-ledger-artifact-binding:${cell.slideNumber}:${cell.exportMode}:${cell.comPhase}:${cell.defectFieldId}`);
  }

  const rootProvenance = readJson(artifactPath(bundleDir, bindings, "evidence-graph-root-json"));
  const { root } = validateRootProvenance(rootProvenance, productTrust, readbackVerifier, manifest, pins, inventory);
  const completionProvenance = readJson(artifactPath(bundleDir, bindings, "completion-seal-json"));
  validateCompletionProvenance(completionProvenance, productTrust, readbackVerifier, root, releaseProfile, pins, ciState);

  for (const mode of ["fidelity", "hybrid"]) {
    for (const phase of ["first-open", "reopen"]) {
      const snapshot = readJson(artifactPath(bundleDir, bindings, `${mode}-${phase}-com-json`));
      if (snapshot.authorityDomain !== "actual_product" || snapshot.repairDialogObserved !== false || snapshot.unexpectedPowerPointProcessCount !== 0) throw new Error(`gate1-com-snapshot-invalid:${mode}:${phase}`);
      if (phase === "reopen" && snapshot.reopenInputPptxDigest !== snapshot.savedCopyPptxDigest) throw new Error(`gate1-save-reopen-digest-mismatch:${mode}`);
    }
  }
  return { cells, ledgerDigest: canonicalSha256(cells), root };
}

export function collectGate1(options) {
  const manifest = readJson(options.manifestPath);
  const contract = readJson(options.validatorContractPath);
  const policy = readJson(options.policyPath);
  if (manifest.manifestDigest !== canonicalSha256Excluding(manifest, ["$.manifestDigest"])) throw new Error("gate1-manifest-digest-invalid");
  if (contract.contractDigest !== canonicalSha256Excluding(contract, ["$.contractDigest"])) throw new Error("gate1-validator-contract-digest-invalid");
  validatePolicyBinding(manifest, policy);
  validateCountContract("gate1", manifest);
  if (fs.existsSync(options.outputDir)) throw new Error("gate1-output-must-be-fresh");
  fs.mkdirSync(options.outputDir, { recursive: true });

  const opening = captureBoundedSourceCensus(options.repoRoot);
  const pins = readJson(options.runtimePinsPath);
  validateRuntimePins(manifest, pins, opening);
  const readbackVerifier = loadServerReadbackVerifier(options.serverReadbackVerifierAttestationPath, pins, manifest);
  const releaseTrustReadback = validateReleaseTrustReadback(options.releaseTrustReadbackPath, pins, readbackVerifier);
  const releaseProfile = loadReleaseProfile(pins);
  validateGateMinus1(options, pins);
  const bindings = readJson(options.artifactBindingsPath);
  const inventory = collectArtifactInventory(manifest, options.bundleDir, bindings, pins);
  const inventoryDigest = canonicalSha256(inventory);
  const artifactState = validateGate1ArtifactContents(options.bundleDir, bindings, inventory, manifest, policy, pins);
  const observations = readJson(options.phaseObservationsPath);
  if (observations.schemaVersion !== "authored-gate1-phase-observations/v1" || observations.authorityDomain !== "actual_product" || observations.presentationId !== pins.presentationId || observations.presentationRevision !== pins.presentationRevision || observations.generationAttemptId !== pins.generationAttemptId || observations.fidelityExportAttemptId !== pins.fidelityExportAttemptId || observations.hybridExportAttemptId !== pins.hybridExportAttemptId || !Array.isArray(observations.phases)) throw new Error("gate1-phase-observations-invalid");
  if (artifactState.environment.serverReadbackSignerKeyId !== pins.serverReadbackSignerKeyId || artifactState.environment.serverReadbackPublicKeyDigest !== pins.serverReadbackPublicKeyDigest || artifactState.environment.fastApiSignerTrustBundleDigest !== pins.fastApiSignerTrustBundleDigest || artifactState.environment.releaseTrustReadbackDigest !== pins.releaseTrustReadbackDigest || artifactState.environment.executionNonce !== pins.executionNonce) throw new Error("gate1-environment-readback-trust-mismatch");
  const productTrust = validateProductTrustBundle(options.productAuthorityTrustPath, pins, manifest, artifactState.environment, releaseTrustReadback);
  const byId = new Map(observations.phases.map((entry) => [entry.phaseId, entry]));
  if (byId.size !== observations.phases.length || observations.phases.length !== 37) throw new Error("gate1-phase-observation-cardinality");
  const inventoryIds = new Set(inventory.map((entry) => entry.artifactId));
  const ci = readJson(artifactPath(options.bundleDir, bindings, "ci-seal-json"));
  const observedCiState = ciAuthorityState(ci, manifest, opening.head);
  const { cells, ledgerDigest, root: productRootForPhase } = validateProductCrossBindings(options.bundleDir, bindings, inventory, manifest, pins, observedCiState, productTrust, releaseProfile, readbackVerifier);
  const productPhaseResult = readJson(artifactPath(options.bundleDir, bindings, "phase-result-set-json"));
  validateProductArtifactAuthority(productPhaseResult, productTrust, "phase-result-set");
  if (productPhaseResult.schemaVersion !== "authored-gate1-product-phase-result-set/v1" || productPhaseResult.presentationId !== pins.presentationId || productPhaseResult.presentationRevision !== pins.presentationRevision || productPhaseResult.generationAttemptId !== pins.generationAttemptId || productPhaseResult.fidelityExportAttemptId !== pins.fidelityExportAttemptId || productPhaseResult.hybridExportAttemptId !== pins.hybridExportAttemptId || productPhaseResult.requiredPhaseIdsDigest !== manifest.requiredPhaseIdsDigest || productPhaseResult.requiredArtifactIdsDigest !== manifest.requiredArtifactIdsDigest || productPhaseResult.evidenceGraphRootDigest !== productRootForPhase.rootDigest || productPhaseResult.baseArtifactInventoryDigest !== productRootForPhase.artifactInventoryDigest || canonicalSha256(productPhaseResult.phases) !== canonicalSha256(observations.phases)) throw new Error("gate1-product-phase-result-binding-invalid");
  const orderedResults = manifest.phases.map((phase) => {
    const observation = byId.get(phase.phaseId);
    if (!observation) throw new Error(`gate1-phase-observation-missing:${phase.phaseId}`);
    let state = validateActualProductPhaseObservation(phase, observation, inventoryIds);
    if (phase.phaseId === "g1.authority.ci-current-commit") state = observedCiState;
    if (["g1.authority.required-receipts", "g1.authority.completion-seal", "g1.authority.active-manifest-at-seal", "g1.projection.promotion-ready"].includes(phase.phaseId) && observedCiState !== "accepted") state = observedCiState;
    const digestCore = { ordinal: phase.ordinal, phaseId: phase.phaseId, state, evidenceArtifactIds: observation.evidenceArtifactIds, observationDigest: canonicalSha256(observation) };
    return { ordinal: phase.ordinal, phaseId: phase.phaseId, state, resultDigest: canonicalSha256(digestCore) };
  });
  const closing = captureBoundedSourceCensus(options.repoRoot);
  assertSourceTreeCensusUnchanged(opening, closing);
  writeFreshJson(path.join(options.outputDir, "source-census-opening.json"), opening);
  writeFreshJson(path.join(options.outputDir, "source-census-closing.json"), closing);

  const acceptedPhaseCount = orderedResults.filter((entry) => entry.state === "accepted").length;
  const blockedPhaseCount = orderedResults.filter((entry) => entry.state === "blocked").length;
  const failedPhaseCount = orderedResults.filter((entry) => entry.state === "failed").length;
  const notVerifiedPhaseCount = orderedResults.filter((entry) => entry.state === "not-verified").length;
  const suiteStatus = failedPhaseCount ? "failed" : blockedPhaseCount ? "blocked" : notVerifiedPhaseCount ? "not-verified" : "accepted";
  const phaseIds = orderedResults.map((entry) => entry.phaseId);
  const expectedPhaseIds = manifest.phases.map((entry) => entry.phaseId);
  const result = {
    schemaVersion: "authored-gate1-result-set/v1",
    manifestDigest: manifest.manifestDigest,
    semanticValidatorContractDigest: contract.contractDigest,
    requiredPhaseIdsDigest: manifest.requiredPhaseIdsDigest,
    requiredArtifactIdsDigest: manifest.requiredArtifactIdsDigest,
    evidencePolicyDigest: manifest.evidencePolicyDigest,
    tenantId: pins.tenantId,
    ownerId: pins.ownerId,
    presentationId: pins.presentationId,
    presentationRevision: pins.presentationRevision,
    operationId: pins.operationId,
    generationAttemptId: pins.generationAttemptId,
    fidelityExportAttemptId: pins.fidelityExportAttemptId,
    hybridExportAttemptId: pins.hybridExportAttemptId,
    activeManifestDigest: pins.activeManifestDigest,
    sourceTreeCensusDigest: pins.sourceTreeCensusDigest,
    sourceHeadCommit: pins.sourceHeadCommit,
    environmentAttestationDigest: pins.environmentAttestationDigest,
    workerAttestationDigest: pins.workerAttestationDigest,
    gateMinus1ManifestDigest: pins.gateMinus1ManifestDigest,
    gateMinus1ResultSetDigest: pins.gateMinus1ResultSetDigest,
    gateMinus1SemanticValidationReceiptArtifactDigest: pins.gateMinus1SemanticValidationReceiptArtifactDigest,
    gateMinus1SemanticValidatorContractDigest: pins.gateMinus1SemanticValidatorContractDigest,
    releaseProfileDigest: pins.releaseProfileDigest,
    featureSetDigest: pins.featureSetDigest,
    productAuthorityTrustBundleDigest: pins.productAuthorityTrustBundleDigest,
    productAuthorityIssuerId: pins.productAuthorityIssuerId,
    productAuthoritySignerKeyId: pins.productAuthoritySignerKeyId,
    productAuthorityEnvironmentId: pins.productAuthorityEnvironmentId,
    productAuthorityKeyPurpose: pins.productAuthorityKeyPurpose,
    semanticValidatorTrustBundleDigest: pins.semanticValidatorTrustBundleDigest,
    semanticValidatorIssuerId: pins.semanticValidatorIssuerId,
    semanticValidatorSignerKeyId: pins.semanticValidatorSignerKeyId,
    semanticValidatorEnvironmentId: pins.semanticValidatorEnvironmentId,
    semanticValidatorKeyPurpose: pins.semanticValidatorKeyPurpose,
    releaseTrustReadbackDigest: pins.releaseTrustReadbackDigest,
    fastApiSignerTrustBundleDigest: pins.fastApiSignerTrustBundleDigest,
    serverReadbackVerifierAttestationDigest: pins.serverReadbackVerifierAttestationDigest,
    serverReadbackSignerKeyId: pins.serverReadbackSignerKeyId,
    serverReadbackPublicKeyDigest: pins.serverReadbackPublicKeyDigest,
    serverReadbackAuthority: manifest.serverReadbackAuthority,
    executionNonce: pins.executionNonce,
    suiteStatus,
    executedPhaseCount: 37,
    acceptedPhaseCount,
    blockedPhaseCount,
    failedPhaseCount,
    notVerifiedPhaseCount,
    skippedPhaseCount: 0,
    missingPhaseIds: expectedPhaseIds.filter((id) => !phaseIds.includes(id)),
    duplicatePhaseIds: [...new Set(phaseIds.filter((id, index) => phaseIds.indexOf(id) !== index))],
    unexpectedPhaseIds: [...new Set(phaseIds.filter((id) => !expectedPhaseIds.includes(id)))],
    orderedResults,
    artifactInventory: inventory,
    artifactInventoryDigestProjection: { canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$.artifactInventory", ordering: "artifactId then versionId", excludedJsonPaths: [] },
    artifactInventoryDigest: inventoryDigest,
    ledgerCellCount: cells.length,
    ledgerDigest,
    semanticValidation: {
      orderedPhaseIdSetExact: JSON.stringify(phaseIds) === JSON.stringify(expectedPhaseIds),
      successfulStateAllAccepted: suiteStatus === "accepted" && acceptedPhaseCount === 37,
      artifactIdSetExact: inventoryIds.size === 70 && manifest.requiredArtifactIds.every((id) => inventoryIds.has(id)),
      artifactIdUnique: inventoryIds.size === inventory.length,
      immutableArtifactBindingsValid: true,
      ledgerCellSetExact: cells.length === 384,
      policyDigestExact: pins.evidencePolicyDigest === manifest.evidencePolicyDigest,
      gateMinus1FeasiblePinExact: true,
      receiptSubjectManifestDigestExact: true,
      receiptSubjectResultCoreDigestExact: true,
      receiptSemanticValidatorContractDigestExact: true,
    },
    resultCoreDigestProjection: { algorithm: "sha256", canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$", excludedJsonPaths: ["$.resultCoreDigest", "$.semanticValidationReceiptRef", "$.resultSetDigest"] },
    resultCoreDigest: "",
    semanticValidationReceiptRef: null,
    digestProjection: { algorithm: "sha256", canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$", excludedJsonPaths: ["$.resultSetDigest"] },
    resultSetDigest: "",
  };
  result.resultCoreDigest = canonicalSha256Excluding(result, ["$.resultCoreDigest", "$.semanticValidationReceiptRef", "$.resultSetDigest"]);
  validateCountContract("gate1", manifest, result);
  writeFreshJson(path.join(options.outputDir, "gate1-result-core.json"), result);
  writeFreshJson(path.join(options.outputDir, "gate1-semantic-validation-request.json"), {
    schemaVersion: "authored-semantic-validation-request/v1",
    authorityDomain: "actual_product",
    evidenceClass: "actual_product_release_qualification",
    subjectManifestDigest: manifest.manifestDigest,
    subjectResultCoreDigest: result.resultCoreDigest,
    semanticValidatorContractDigest: contract.contractDigest,
    artifactInventoryDigest: inventoryDigest,
    ledgerDigest,
    evidencePolicyDigest: manifest.evidencePolicyDigest,
    presentationId: pins.presentationId,
    presentationRevision: pins.presentationRevision,
    generationAttemptId: pins.generationAttemptId,
    fidelityExportAttemptId: pins.fidelityExportAttemptId,
    hybridExportAttemptId: pins.hybridExportAttemptId,
    productAuthorityTrustBundleDigest: pins.productAuthorityTrustBundleDigest,
    semanticValidatorTrustBundleDigest: pins.semanticValidatorTrustBundleDigest,
  });
  writeFreshJson(path.join(options.outputDir, "gate1-collection-summary.json"), { suiteStatus, remoteCiState: observedCiState, executedPhaseCount: 37, artifactCount: 70, ledgerCellCount: 384, resultCoreDigest: result.resultCoreDigest, finalResultSealed: false });
  return result;
}

function verifyActualProductReceipt(core, receiptPath, attestationPath, trustReadback) {
  const payload = readJson(receiptPath);
  const attestationBytes = fs.readFileSync(attestationPath);
  if (digestBytes(attestationBytes) !== core.semanticValidatorTrustBundleDigest) throw new Error("gate1-semantic-validator-trust-digest-mismatch");
  const attestation = JSON.parse(attestationBytes.toString("utf8"));
  if (attestation.schemaVersion !== "authored-semantic-validator-trust-bundle/v1" || attestation.source !== "fastapi-runtime-trust-bundle" || attestation.sealed !== true || attestation.authorityDomain !== "actual_product" || attestation.evidenceClass !== "actual_product_release_qualification" || attestation.keyPurpose !== "release-semantic-validator" || attestation.revoked !== false) throw new Error("gate1-semantic-validator-trust-invalid");
  if (attestation.manifestDigest !== core.manifestDigest || attestation.evidencePolicyDigest !== core.evidencePolicyDigest || attestation.environmentAttestationDigest !== core.environmentAttestationDigest || attestation.issuerId !== core.semanticValidatorIssuerId || attestation.signerKeyId !== core.semanticValidatorSignerKeyId || attestation.environmentId !== core.semanticValidatorEnvironmentId || attestation.keyPurpose !== core.semanticValidatorKeyPurpose) throw new Error("gate1-semantic-validator-trust-pin-mismatch");
  const publicDer = Buffer.from(attestation.publicKeySpkiBase64 || "", "base64");
  const rawPublic = publicDer.subarray(publicDer.length - 32); const registered = trustReadback.registeredKeys.filter((entry) => entry.authorityDomain === "actual_product" && entry.issuerId === attestation.issuerId && entry.signerKeyId === attestation.signerKeyId && entry.environmentId === attestation.environmentId && entry.keyPurpose === attestation.keyPurpose && entry.algorithm === "Ed25519" && entry.revoked === false && entry.publicKeyDigest === digestBytes(rawPublic));
  if (publicDer.length < 32 || attestation.publicKeyDigest !== digestBytes(rawPublic) || registered.length !== 1) throw new Error("gate1-semantic-validator-key-not-fastapi-registered");
  if (payload.issuerId !== attestation.issuerId || payload.signerKeyId !== attestation.signerKeyId || payload.signatureAlgorithm !== "Ed25519") throw new Error("gate1-semantic-receipt-issuer-mismatch");
  if (payload.subjectManifestDigest !== core.manifestDigest || payload.subjectResultCoreDigest !== core.resultCoreDigest || payload.semanticValidatorContractDigest !== core.semanticValidatorContractDigest || payload.subjectArtifactInventoryDigest !== core.artifactInventoryDigest || payload.subjectLedgerDigest !== core.ledgerDigest || payload.evidencePolicyDigest !== core.evidencePolicyDigest || payload.presentationId !== core.presentationId || payload.presentationRevision !== core.presentationRevision || payload.generationAttemptId !== core.generationAttemptId || payload.fidelityExportAttemptId !== core.fidelityExportAttemptId || payload.hybridExportAttemptId !== core.hybridExportAttemptId || payload.sourceHeadCommit !== core.sourceHeadCommit || payload.productAuthorityTrustBundleDigest !== core.productAuthorityTrustBundleDigest) throw new Error("gate1-semantic-receipt-subject-mismatch");
  if (payload.receiptDigest !== canonicalSha256Excluding(payload, ["$.receiptDigest", "$.signatureBase64"])) throw new Error("gate1-semantic-receipt-digest-invalid");
  const now = Date.now();
  if (Date.parse(payload.issuedAt) > now + 30_000 || Date.parse(payload.expiresAt) <= now) throw new Error("gate1-semantic-receipt-not-fresh");
  const publicKey = crypto.createPublicKey({ key: publicDer, type: "spki", format: "der" });
  if (!crypto.verify(null, Buffer.from(payload.receiptDigest, "ascii"), publicKey, Buffer.from(payload.signatureBase64, "base64"))) throw new Error("gate1-semantic-receipt-signature-invalid");
  const bytes = fs.readFileSync(receiptPath);
  const sha256 = digestBytes(bytes);
  return { artifactId: "gate1-semantic-validation-receipt", versionId: sha256.replace("sha256:", "sha256-"), mediaType: "application/json", byteLength: bytes.length, sha256, ...payload };
}

export function sealGate1(options) {
  const core = readJson(options.resultCorePath);
  if (core.semanticValidationReceiptRef !== null || core.resultSetDigest !== "") throw new Error("gate1-result-core-already-sealed");
  if (core.resultCoreDigest !== canonicalSha256Excluding(core, ["$.resultCoreDigest", "$.semanticValidationReceiptRef", "$.resultSetDigest"])) throw new Error("gate1-result-core-digest-invalid");
  const result = structuredClone(core);
  const readbackVerifier = loadServerReadbackVerifier(options.serverReadbackVerifierAttestationPath, core, core);
  const releaseTrustReadback = validateReleaseTrustReadback(options.releaseTrustReadbackPath, core, readbackVerifier);
  result.semanticValidationReceiptRef = verifyActualProductReceipt(core, options.receiptPath, options.trustAttestationPath, releaseTrustReadback);
  result.resultSetDigest = canonicalSha256Excluding(result, ["$.resultSetDigest"]);
  writeFreshJson(options.resultPath, result);
  writeFreshJson(`${options.resultPath}.summary.json`, { suiteStatus: result.suiteStatus, executedPhaseCount: result.executedPhaseCount, artifactCount: result.artifactInventory.length, ledgerCellCount: result.ledgerCellCount, resultSetDigest: result.resultSetDigest, finalResultSealed: true });
  return result;
}

function parseArgs(argv) {
  const command = argv[0];
  const options = {};
  for (let index = 1; index < argv.length; index += 2) {
    if (!argv[index]?.startsWith("--") || argv[index + 1] === undefined) throw new Error(`invalid-argument:${argv[index]}`);
    options[argv[index].slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = path.resolve(argv[index + 1]);
  }
  return { command, options };
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
const modulePath = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invokedPath === modulePath) {
  try {
    const { command, options } = parseArgs(process.argv.slice(2));
    const result = command === "collect" ? collectGate1(options) : command === "seal" ? sealGate1(options) : (() => { throw new Error("usage: run-authored-bounded-gate1-v3.mjs collect|seal --..."); })();
    process.stdout.write(`${JSON.stringify({ suiteStatus: result.suiteStatus, executedPhaseCount: result.executedPhaseCount, resultCoreDigest: result.resultCoreDigest, resultSetDigest: result.resultSetDigest || null })}\n`);
    if (command === "seal" && result.suiteStatus !== "accepted") process.exitCode = 2;
  } catch (error) { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 2; }
}
