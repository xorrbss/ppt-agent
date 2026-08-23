import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { assertSourceTreeCensusUnchanged } from "../authored-bounded-release-contracts.mjs";
import { captureBoundedSourceCensus } from "../run-authored-bounded-gate0-v3.mjs";

const DIGEST_RE = /^sha256:[0-9a-f]{64}$/;
const TIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

export function canonicalize(value) {
  if (value === null || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "string") return JSON.stringify(value.normalize("NFC"));
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value) || Object.is(value, -0)) throw new Error(`non-canonical-number:${value}`);
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (!value || typeof value !== "object") throw new Error(`unsupported-canonical-type:${typeof value}`);
  const entries = Object.entries(value).map(([key, child]) => [key.normalize("NFC"), child]);
  entries.sort(([a], [b]) => Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8")));
  for (let i = 1; i < entries.length; i += 1) {
    if (entries[i - 1][0] === entries[i][0]) throw new Error(`normalized-duplicate-key:${entries[i][0]}`);
  }
  return `{${entries.map(([key, child]) => `${JSON.stringify(key)}:${canonicalize(child)}`).join(",")}}`;
}

export function digestBytes(bytes) {
  return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
}

export function digestObject(value) {
  return digestBytes(Buffer.from(canonicalize(value), "utf8"));
}

function cloneWithout(value, excludedTopLevelKeys) {
  const copy = structuredClone(value);
  for (const key of excludedTopLevelKeys) delete copy[key];
  return copy;
}

function digestProjected(value, excludedTopLevelKeys) {
  return digestObject(cloneWithout(value, excludedTopLevelKeys));
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
}

function fileRecord(filePath, artifactId, mediaType, locator, retentionClass = "gate-minus1-30d") {
  const bytes = fs.readFileSync(filePath);
  const sha256 = digestBytes(bytes);
  return {
    artifactId,
    versionId: sha256.replace("sha256:", "sha256-"),
    mediaType,
    byteLength: bytes.length,
    sha256,
    locator,
    retentionClass,
  };
}

function rfc3339(date = new Date()) {
  return date.toISOString();
}

function base32Upper(bytes) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = 0;
  let value = 0;
  let output = "";
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      output += alphabet[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) output += alphabet[(value << (5 - bits)) & 31];
  return output;
}

export function qidCarrier(prefix, value) {
  return `${prefix}2_${base32Upper(Buffer.from(value, "utf8"))}`;
}

function manifestRequiredIdsDigest(cases) {
  return digestObject(cases.slice().sort((a, b) => a.ordinal - b.ordinal).map((entry) => entry.caseId));
}

function roleSetDigest(roles) {
  return digestObject(roles);
}

function mediaTypeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".pptx") return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  if (ext === ".ps1") return "text/x-powershell";
  if (ext === ".mjs") return "text/javascript";
  if (ext === ".png") return "image/png";
  return "application/json";
}

function prepare(options) {
  const reference = readJson(options.referenceManifest);
  const environment = readJson(options.environmentReceipt);
  const census = readJson(options.sourceTreeCensus);
  const policy = readJson(options.policy);
  assert.equal(census.schemaVersion, "SourceTreeCensusV1", "source-tree-census-schema");
  assert.ok(DIGEST_RE.test(census.censusDigest), "source-tree-census-digest");
  assert.ok(DIGEST_RE.test(census.indexTreeDigest), "source-tree-index-digest");
  assert.ok(DIGEST_RE.test(census.generatedCachePolicyDigest), "source-tree-generated-cache-policy-digest");
  assert.equal(digestProjected(policy, ["policyDigest"]), policy.policyDigest, "policy digest mismatch");
  const createdAt = fs.statSync(options.inputPptx).mtime.toISOString();
  const manifestSealedAt = rfc3339(new Date(Date.now() + 10));
  const issuedAt = rfc3339(new Date(Date.now() + 20));
  const expiresAt = rfc3339(new Date(Date.now() + policy.freshness.environmentAttestationMaxAgeSeconds * 1000));
  const inputRecord = fileRecord(options.inputPptx, "gate-minus1-input-pptx", mediaTypeFor(options.inputPptx), "input/gate-minus1-input.pptx");
  delete inputRecord.retentionClass;
  inputRecord.role = "input-pptx";
  const attemptId = path.basename(options.outputDir);
  const carrierValues = readJson(options.carrierValues);
  assert.equal(carrierValues.attemptId, attemptId);
  const planDigest = carrierValues.planDigest;
  const exportAttemptId = carrierValues.exportAttemptId;
  const finalReference = structuredClone(reference);
  finalReference.manifestId = `authored-gate-minus1-com-feasibility.${attemptId}.v1`;
  finalReference.manifestStatus = "sealed-for-execution";
  finalReference.policyCandidateId = policy.policyId;
  finalReference.policyCandidateDigest = policy.policyDigest;
  finalReference.requiredEnvironmentDigest = environment.environmentReceiptDigest;
  finalReference.inputArtifacts = [inputRecord];
  finalReference.requiredCaseIdsDigest = manifestRequiredIdsDigest(finalReference.cases);
  for (const entry of finalReference.cases) {
    entry.inputArtifactIds = [inputRecord.artifactId];
    entry.inputArtifactDigest = inputRecord.sha256;
  }
  delete finalReference.manifestDigest;
  delete finalReference.executionPins;
  const manifestCoreDigest = digestObject(finalReference);

  const rolePaths = [
    ["input-pptx", options.inputPptx, "gate-minus1-input-pptx"],
    ["probe-script", options.probeScript, "gate-minus1-probe-script"],
    ["dialog-watcher-script", options.watcherScript, "gate-minus1-dialog-watcher-script"],
    ["cleanup-census-script", options.censusScript, "gate-minus1-cleanup-census-script"],
    ["environment-receipt", options.environmentReceipt, "gate-minus1-environment-receipt"],
    ["source-tree-census", options.sourceTreeCensus, "gate-minus1-source-tree-census"],
  ];
  const pinnedArtifacts = rolePaths.map(([role, filePath, artifactId]) => ({
    ...fileRecord(filePath, artifactId, mediaTypeFor(filePath), path.relative(options.outputDir, filePath).replaceAll("\\", "/")),
    role,
  })).map(({ retentionClass: _, ...entry }) => entry);
  const roles = pinnedArtifacts.map((entry) => entry.role);
  const expectedRoleDigest = reference.executionSealPolicy.executionArtifactRoleSetDigest;
  assert.equal(roleSetDigest(roles), expectedRoleDigest, "execution artifact role-set digest mismatch");

  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  const signerKeyId = digestBytes(publicDer);
  const seal = {
    schemaVersion: "authored-gate-minus1-execution-seal/v1",
    executionSealId: `gminus1-seal-${crypto.randomUUID()}`,
    manifestCoreDigestProjection: { algorithm: "sha256", canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$", excludedJsonPaths: ["$.executionPins", "$.manifestDigest"] },
    manifestCoreDigest,
    policyCandidateDigest: policy.policyDigest,
    freshInputCreatedAt: createdAt,
    manifestSealedAt,
    inputArtifactDigest: inputRecord.sha256,
    environmentReceiptDigest: environment.environmentReceiptDigest,
    sourceTreeCensusDigest: census.censusDigest,
    pinnedArtifactRoleSetDigest: expectedRoleDigest,
    pinnedArtifacts,
    executionSealSignerId: `gate-minus1-actual-probe/${signerKeyId}`,
    executionSealSignatureAlgorithm: "Ed25519",
    executionSealSignatureInput: { encoding: "ascii", sourceField: "$.executionSealDigest", exactByteLength: 71, byteContract: "ASCII bytes of the exact lowercase string sha256:<64 lowercase hexadecimal digits>; no BOM, terminator, whitespace, or newline" },
    executionSealSignatureBase64: "",
    executionSealNonce: crypto.randomBytes(24).toString("hex"),
    executionSealIssuedAt: issuedAt,
    executionSealExpiresAt: expiresAt,
    executionSealDigestProjection: { algorithm: "sha256", canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$", excludedJsonPaths: ["$.executionSealDigest", "$.executionSealSignatureBase64"] },
    executionSealDigest: "",
  };
  seal.executionSealDigest = digestProjected(seal, ["executionSealDigest", "executionSealSignatureBase64"]);
  seal.executionSealSignatureBase64 = crypto.sign(null, Buffer.from(seal.executionSealDigest, "ascii"), privateKey).toString("base64");
  assert.equal(Buffer.from(seal.executionSealDigest, "ascii").length, 71);
  assert.equal(crypto.verify(null, Buffer.from(seal.executionSealDigest, "ascii"), publicKey, Buffer.from(seal.executionSealSignatureBase64, "base64")), true);

  const sealPath = path.join(options.outputDir, "authority", "execution-seal.json");
  writeJson(sealPath, seal);
  const sealRecord = fileRecord(sealPath, "gate-minus1-execution-seal", "application/json", "authority/execution-seal.json");
  const executionSealArtifactRef = {
    ...sealRecord,
    schemaId: "urn:presenton:authored:gate-minus1-execution-seal:v1",
    payloadExecutionSealDigest: seal.executionSealDigest,
  };
  delete executionSealArtifactRef.retentionClass;
  finalReference.executionPins = executionSealArtifactRef;
  finalReference.manifestDigest = digestProjected(finalReference, ["manifestDigest"]);
  const manifestPath = path.join(options.outputDir, "authority", "sealed-manifest.json");
  writeJson(manifestPath, finalReference);
  const keyAttestation = {
    schemaVersion: "gate-minus1-probe-key-attestation/v1",
    trustDomain: "gate_minus1_actual_probe",
    signerKeyId,
    publicKeySpkiBase64: publicDer.toString("base64"),
    createdAt: issuedAt,
    privateKeyPersisted: false,
  };
  writeJson(path.join(options.outputDir, "authority", "signer-key-attestation.json"), keyAttestation);
  fs.writeFileSync(path.join(options.outputDir, "authority", ".ephemeral-private-key.pem"), privateKey.export({ type: "pkcs8", format: "pem" }), { flag: "wx", mode: 0o600 });
  const identity = { attemptId, manifestPath, manifestDigest: finalReference.manifestDigest, inputPptxDigest: inputRecord.sha256, environmentReceiptDigest: environment.environmentReceiptDigest, executionSealPath: sealPath, executionSealArtifactRef, planDigest, exportAttemptId };
  writeJson(path.join(options.outputDir, "authority", "execution-identity.json"), identity);
  process.stdout.write(`${JSON.stringify(identity)}\n`);
}

function observationRuleMap(contract) {
  const map = new Map();
  for (const rule of contract.observationValueContractRules.rules) {
    for (const fieldId of rule.fieldIds) {
      if (map.has(fieldId)) throw new Error(`duplicate-observation-rule:${fieldId}`);
      map.set(fieldId, { valueType: rule.valueType, unit: rule.unit, scaleInteger: rule.scaleInteger });
    }
  }
  return map;
}

function artifactFromObject(outputDir, artifactId, object, locator) {
  const filePath = path.join(outputDir, locator);
  writeJson(filePath, object);
  return fileRecord(filePath, artifactId, "application/json", locator);
}

function validateObservationValue(fieldId, value, rule) {
  if (!rule) throw new Error(`unregistered-observation:${fieldId}`);
  if (rule.valueType === "integer" && !Number.isSafeInteger(value)) throw new Error(`invalid-integer:${fieldId}`);
  if (rule.valueType === "boolean" && typeof value !== "boolean") throw new Error(`invalid-boolean:${fieldId}`);
  if ((rule.valueType === "string" || rule.valueType === "utf16-le-hex") && typeof value !== "string") throw new Error(`invalid-string:${fieldId}`);
  if (rule.valueType === "digest" && !DIGEST_RE.test(value)) throw new Error(`invalid-digest:${fieldId}`);
  if (rule.valueType === "utf16-le-hex" && !/^(?:[0-9a-f]{4})*$/.test(value)) throw new Error(`invalid-utf16-hex:${fieldId}`);
}

function sameCanonical(left, right) {
  try { return canonicalize(left) === canonicalize(right); } catch { return false; }
}

function invariant(errors, condition, code) {
  if (!condition) errors.push(code);
}

function renderMetricsAreStable(metrics) {
  return metrics &&
    Number.isSafeInteger(metrics.dimensions?.width) && metrics.dimensions.width > 0 &&
    Number.isSafeInteger(metrics.dimensions?.height) && metrics.dimensions.height > 0 &&
    Array.isArray(metrics.firstOpen) && metrics.firstOpen.length > 0 &&
    sameCanonical(metrics.firstOpen, metrics.reopen) &&
    metrics.firstOpen.every((entry) => Number.isSafeInteger(entry.slideNumber) && DIGEST_RE.test(entry.sha256));
}

function uniqueStrings(values) {
  return Array.isArray(values) && values.every((value) => typeof value === "string") && new Set(values).size === values.length;
}

function mapByPart(entries = []) {
  return new Map(entries.map((entry) => [entry.partName, entry]));
}

export function validateOoxmlReadbackFromPptx(pptxPath, readback) {
  const listed = execFileSync("tar", ["-tf", pptxPath], { maxBuffer: 64 * 1024 * 1024 })
    .toString("utf8").split(/\r?\n/).map((entry) => entry.replaceAll("\\", "/").replace(/^\.\//, "")).filter((entry) => entry && !entry.endsWith("/")).sort();
  const declared = (readback.parts ?? []).map((entry) => entry.partName).sort();
  assert.deepEqual(listed, declared, `ooxml-part-census:${path.basename(pptxPath)}`);
  const bytesByPart = new Map();
  for (const part of readback.parts) {
    if (/[\[\]*?]/.test(part.partName)) continue; // bsdtar treats these exact OPC names as patterns; the archive census still binds them.
    const bytes = execFileSync("tar", ["-xOf", pptxPath, part.partName], { maxBuffer: 64 * 1024 * 1024 });
    assert.equal(bytes.length, part.byteLength, `ooxml-part-length:${part.partName}`);
    assert.equal(digestBytes(bytes), part.sha256, `ooxml-part-digest:${part.partName}`);
    bytesByPart.set(part.partName, bytes);
  }
  return bytesByPart;
}

function shapeXml(slideXml, shapeName) {
  return (String(slideXml ?? "").match(/<p:sp>[\s\S]*?<\/p:sp>/g) ?? []).find((fragment) => fragment.includes(`name="${shapeName}"`)) ?? "";
}

function expectedDisposition(caseId) {
  const bounded = new Set([
    "gminus1.text.combining-nfc", "gminus1.text.east-asian-typeface", "gminus1.text.small-overflow",
    "gminus1.font.saved-embedded-parts", "gminus1.paint.p-bg-roundtrip",
    "gminus1.chart.line-style-roundtrip", "gminus1.chart.pie-point-style-roundtrip",
    "gminus1.com.repair-dialog-witness",
  ]);
  if (caseId === "gminus1.text.autofit-shrink") return "raster-required";
  return bounded.has(caseId) ? "native-observable-bounded" : "native-observable";
}

export function evaluateCaseInvariant(caseId, fields, evidence) {
  const errors = [];
  const first = evidence.first ?? {};
  const reopen = evidence.reopen ?? {};
  const inputOoxml = evidence.inputOoxml ?? {};
  const savedOoxml = evidence.savedOoxml ?? {};
  const fontInventory = evidence.fontInventory ?? {};
  const raw = evidence.raw ?? {};
  const carrier = evidence.carrier ?? {};
  const inputSlide1Xml = evidence.inputSlide1Xml ?? "";
  const savedSlide1Xml = evidence.savedSlide1Xml ?? "";
  const stableText = (shapeName) => sameCanonical(first.texts?.[shapeName], reopen.texts?.[shapeName]);
  const stableChart = (shapeName) => sameCanonical(first.charts?.[shapeName], reopen.charts?.[shapeName]);
  const stableRender = (value) => renderMetricsAreStable(value);
  const exact = (actual, expected, code) => invariant(errors, sameCanonical(actual, expected), `${caseId}:${code}`);
  const yes = (condition, code) => invariant(errors, condition, `${caseId}:${code}`);

  switch (caseId) {
    case "gminus1.text.utf16-surrogate":
      exact(fields.sourceUtf16LeHex, "41003dd880de4200", "source-utf16");
      exact(fields.emittedUtf16LeHex, "41003dd880de4200", "emitted-utf16");
      exact(fields.comTextUtf16LeHex, "41003dd880de4200", "com-utf16");
      exact(fields.comLength, 4, "com-length");
      exact(fields.charactersStartLengthText?.pair, { start: 2, length: 2, text: "🚀", utf16LeHex: "3dd880de" }, "surrogate-pair-range");
      yes(fields.firstReopenEquality === true && stableText("case-utf16-surrogate"), "first-reopen-equality");
      break;
    case "gminus1.text.combining-nfc":
      exact(fields.sourceUtf16LeHex, "65000103", "source-decomposed");
      exact(fields.normalizedNfcUtf16LeHex, "e900", "normalized-nfc");
      exact(fields.comTextUtf16LeHex, "e900", "com-nfc");
      yes(fields.firstReopenEquality === true && stableText("case-combining-nfc"), "first-reopen-equality");
      break;
    case "gminus1.text.newline-crlf":
      exact(fields.sourceNewlineTokens, ["CRLF"], "source-token");
      exact(fields.ooxmlParagraphTokens, ["a:p", "a:p"], "ooxml-paragraphs");
      exact(fields.comTextUtf16LeHex, "41006c007000680061000d004200650074006100", "com-cr");
      yes(Array.isArray(fields.paragraphTerminatorRanges) && fields.paragraphTerminatorRanges.some((entry) => entry.text === "\r") && fields.paragraphTerminatorRanges.every((entry) => entry.text !== "\u000b"), "paragraph-range");
      yes((shapeXml(inputSlide1Xml, "case-newline-crlf").match(/<a:p>/g) ?? []).length === 2 && !shapeXml(inputSlide1Xml, "case-newline-crlf").includes("<a:br"), "actual-ooxml-paragraphs");
      yes(shapeXml(inputSlide1Xml, "case-newline-crlf") === shapeXml(savedSlide1Xml, "case-newline-crlf"), "ooxml-roundtrip");
      yes(fields.firstReopenEquality === true && stableText("case-newline-crlf"), "first-reopen-equality");
      break;
    case "gminus1.text.soft-break":
      exact(fields.ooxmlBreakTokens, ["a:br"], "ooxml-break");
      exact(fields.comTextUtf16LeHex, "53006f00660074000b0042007200650061006b00", "com-vt");
      yes(Array.isArray(fields.softBreakRanges) && fields.softBreakRanges.some((entry) => entry.text === "\u000b") && fields.softBreakRanges.every((entry) => entry.text !== "\r"), "soft-break-range");
      yes((shapeXml(inputSlide1Xml, "case-soft-break").match(/<a:br\b/g) ?? []).length === 1, "actual-ooxml-soft-break");
      yes(shapeXml(inputSlide1Xml, "case-soft-break") === shapeXml(savedSlide1Xml, "case-soft-break"), "ooxml-roundtrip");
      yes(fields.firstReopenEquality === true && stableText("case-soft-break"), "first-reopen-equality");
      break;
    case "gminus1.text.empty-repeated-whitespace":
      exact(fields.ooxmlOrderedTextTokens, ["same", "  ", "same", "", "   ", "end"], "ordered-text");
      exact(fields.comTextUtf16LeHex, "730061006d00650020002000730061006d0065000d000d0020002000200065006e006400", "com-whitespace");
      exact(fields.emptyParagraphCount, 1, "empty-paragraph-count");
      yes(Array.isArray(fields.whitespaceRanges) && fields.whitespaceRanges.length === 7, "whitespace-ranges");
      yes((shapeXml(inputSlide1Xml, "case-empty-repeated-whitespace").match(/<a:p>/g) ?? []).length === 3 && shapeXml(inputSlide1Xml, "case-empty-repeated-whitespace").includes("<a:t>same  same</a:t>") && shapeXml(inputSlide1Xml, "case-empty-repeated-whitespace").includes("<a:t>   end</a:t>"), "actual-ooxml-whitespace");
      yes(shapeXml(inputSlide1Xml, "case-empty-repeated-whitespace") === shapeXml(savedSlide1Xml, "case-empty-repeated-whitespace"), "ooxml-roundtrip");
      yes(fields.firstReopenEquality === true && stableText("case-empty-repeated-whitespace"), "first-reopen-equality");
      break;
    case "gminus1.text.mixed-bold-italic":
      exact(fields.wholeRangeTriStateRaw, -2, "whole-range-tristate");
      exact(fields.runBoldRaw, -2, "bold-tristate");
      exact(fields.runItalicRaw, -2, "italic-tristate");
      yes(Array.isArray(fields.runStartLength) && fields.runStartLength.length === 17 && fields.runStartLength.every((entry, index) => entry.start === index + 1 && entry.length === 1), "run-map");
      yes(fields.runFontName === fields.runFontNameFarEast && typeof fields.runFontName === "string" && fields.runFontName.length > 0, "font-family");
      yes(Number.isSafeInteger(fields.runSizeMicroPoint) && fields.runSizeMicroPoint > 0, "font-size");
      yes(fields.firstReopenEquality === true && stableText("case-mixed-bold-italic"), "first-reopen-equality");
      break;
    case "gminus1.text.east-asian-typeface": {
      yes(Array.isArray(fields.runStartLength) && fields.runStartLength.map((entry) => entry.text).join("") === "한글 가나다", "korean-run-map");
      yes(fields.latinFamily === fields.eastAsianFamily && fields.latinFamily === fields.ooxmlLatinTypeface && fields.latinFamily === fields.ooxmlEastAsianTypeface, "family-conjunction");
      yes(shapeXml(inputSlide1Xml, "case-east-asian-typeface").includes(`typeface="${fields.ooxmlLatinTypeface}"`) && shapeXml(inputSlide1Xml, "case-east-asian-typeface") === shapeXml(savedSlide1Xml, "case-east-asian-typeface"), "actual-ooxml-typeface");
      exact(fields.sourceFaceDigest, fontInventory.fontInventoryDigest, "font-inventory-digest");
      exact(fields.embeddedPartDigest, inputOoxml.fontParts, "input-font-parts");
      exact(fields.embeddedPartDigest, savedOoxml.fontParts, "saved-font-parts");
      yes(fields.firstReopenEquality === true && stableText("case-east-asian-typeface"), "first-reopen-equality");
      break;
    }
    case "gminus1.text.autofit-none":
      exact(fields.ooxmlBodyPrAutofit, "noAutofit", "ooxml-autofit");
      exact(fields.comAutoSizeRaw, 0, "com-autosize");
      yes(Number.isSafeInteger(fields.fontSizeMicroPoint) && fields.fontSizeMicroPoint > 0, "font-size");
      yes(Array.isArray(fields.lineBoundsMicroPoint) && fields.lineBoundsMicroPoint.length > 0 && fields.lineBoundsMicroPoint.every((entry) => entry.widthMicroPoint > 0 && entry.heightMicroPoint > 0), "line-bounds");
      yes(shapeXml(inputSlide1Xml, "case-autofit-none").includes("<a:noAutofit/>") && shapeXml(inputSlide1Xml, "case-autofit-none") === shapeXml(savedSlide1Xml, "case-autofit-none"), "actual-ooxml-autofit");
      yes(fields.firstReopenEquality === true && stableText("case-autofit-none"), "first-reopen-equality");
      break;
    case "gminus1.text.autofit-shrink":
      exact(fields.ooxmlBodyPrAutofit, "normAutofit", "ooxml-autofit");
      exact(fields.comAutoSizeRaw, 2, "com-autosize");
      yes(Number.isSafeInteger(fields.requestedFontSizeMicroPoint) && fields.requestedFontSizeMicroPoint > 0, "requested-font-size");
      yes(Number.isSafeInteger(fields.effectiveFontSizeMicroPoint) && fields.effectiveFontSizeMicroPoint > 0, "effective-font-size");
      yes(shapeXml(inputSlide1Xml, "case-autofit-shrink").includes("<a:normAutofit/>") && shapeXml(inputSlide1Xml, "case-autofit-shrink") === shapeXml(savedSlide1Xml, "case-autofit-shrink"), "actual-ooxml-autofit");
      yes(fields.firstReopenEquality === true && stableText("case-autofit-shrink"), "first-reopen-equality");
      break;
    case "gminus1.text.line-bounds-long-ko":
      yes(Number.isSafeInteger(fields.lineCount) && fields.lineCount > 0 && fields.lineCount === fields.lineBoundsMicroPoint?.length, "line-count");
      yes(Number.isSafeInteger(fields.firstReopenDeltaEmu) && fields.firstReopenDeltaEmu >= 0 && fields.firstReopenDeltaEmu <= 254, "line-drift-threshold");
      yes(stableText("case-line-bounds-long-ko"), "first-reopen-line-map");
      yes(stableRender(fields.renderMetricsFixedInteger), "render-equality");
      break;
    case "gminus1.text.small-overflow":
      yes(Array.isArray(fields.shapeRectEmu) && fields.shapeRectEmu.length === 4 && fields.shapeRectEmu.every(Number.isSafeInteger), "shape-rect");
      yes(Array.isArray(fields.marginEmu) && fields.marginEmu.length === 4 && fields.marginEmu.every(Number.isSafeInteger), "margins");
      yes(Number.isSafeInteger(fields.overflowXEmu) && fields.overflowXEmu >= 0 && fields.overflowXEmu <= 3175, "overflow-x-threshold");
      yes(Number.isSafeInteger(fields.overflowYEmu) && fields.overflowYEmu >= 0 && fields.overflowYEmu <= 3175, "overflow-y-threshold");
      yes(stableText("case-small-overflow"), "first-reopen-text");
      yes(stableRender(fields.renderMetricsFixedInteger), "render-equality");
      break;
    case "gminus1.qid.shape-tags-roundtrip": {
      const tagMaps = Object.values(fields.comTagMap ?? {});
      const qids = tagMaps.map((entry) => entry?.PRESENTON_QID_V2);
      yes(tagMaps.length === 3 && uniqueStrings(qids) && qids.every((value) => /^Q2_[A-Z2-7]+$/.test(value)), "qid-unique-uppercase");
      yes(tagMaps.every((entry) => /^K2_[A-Z2-7]+$/.test(entry?.PRESENTON_KIND_V2 ?? "") && /^R2_[A-Z2-7]+$/.test(entry?.PRESENTON_OWNER_ROLE_V2 ?? "")), "kind-role-uppercase");
      exact(fields.decodedSemanticTagMap, fields.comTagMap, "decoded-com-tags");
      yes(fields.firstReopenEquality === true && sameCanonical(first.ownerTags, reopen.ownerTags) && sameCanonical(fields.comTagMap, first.ownerTags), "first-reopen-equality");
      const graph = fields.ooxmlTagRelationshipGraph ?? [];
      exact(graph, inputOoxml.tagGraph, "input-relationship-graph");
      exact(inputOoxml.tagGraph, savedOoxml.tagGraph, "saved-relationship-graph");
      yes(fields.duplicateOrOrphanCount === 0 && inputOoxml.orphanTagPartCount === 0 && savedOoxml.orphanTagPartCount === 0, "no-duplicate-or-orphan");
      yes(graph.every((entry) => entry.duplicateKeyCount === 0) && uniqueStrings(graph.map((entry) => entry.targetPart)), "relationship-unique");
      yes(tagMaps.every((tagMap) => graph.filter((entry) => sameCanonical(entry.tags, tagMap)).length === 1), "relationship-tag-binding");
      break;
    }
    case "gminus1.package.presentation-tags-roundtrip": {
      const expectedMarkers = {
        PRESENTON_HYBRID_PLAN_DIGEST_V2: `D2_${String(carrier.planDigest ?? "").replace("sha256:", "").toUpperCase()}`,
        PRESENTON_EXPORT_ATTEMPT_ID_V2: `U2_${String(carrier.exportAttemptId ?? "").replaceAll("-", "").toUpperCase()}`,
        PRESENTON_EVIDENCE_POLICY_DIGEST_V2: `D2_${String(carrier.policyDigest ?? "").replace("sha256:", "").toUpperCase()}`,
      };
      exact(fields.comTagMap, expectedMarkers, "carrier-values");
      exact(fields.decodedMarkerMap, expectedMarkers, "decoded-markers");
      yes(fields.firstReopenEquality === true && sameCanonical(first.presentationTags, reopen.presentationTags) && sameCanonical(fields.comTagMap, first.presentationTags), "first-reopen-equality");
      const graphMatches = (fields.ooxmlTagRelationshipGraph ?? []).filter((entry) => entry.ownerPart === "ppt/presentation.xml" && sameCanonical(entry.tags, expectedMarkers));
      exact(fields.ooxmlTagRelationshipGraph, inputOoxml.tagGraph, "input-relationship-graph");
      exact(inputOoxml.tagGraph, savedOoxml.tagGraph, "saved-relationship-graph");
      yes(fields.duplicateOrOrphanCount === 0 && graphMatches.length === 1 && graphMatches[0].duplicateKeyCount === 0, "presentation-relationship");
      break;
    }
    case "gminus1.font.saved-embedded-parts": {
      const faces = fontInventory.faces ?? [];
      yes(faces.length === 6, "required-face-count");
      yes(faces.every((face) => face.editableEmbeddingEligible === true && Number.isSafeInteger(face.fsType) && face.fsType === fields.fsType && DIGEST_RE.test(face.sourceContainerSha256) && DIGEST_RE.test(face.extractedSfntSha256) && DIGEST_RE.test(face.cmapCodepointSha256)), "observed-font-fields");
      const source = faces.map((face) => ({ family: face.family, subfamily: face.subfamily, sourceContainerSha256: face.sourceContainerSha256, sourceByteLength: face.sourceByteLength }));
      const extracted = faces.map((face) => ({ family: face.family, subfamily: face.subfamily, ttcIndex: face.ttcIndex, extractedSfntSha256: face.extractedSfntSha256, extractedSfntByteLength: face.extractedSfntByteLength }));
      const cmap = faces.map((face) => ({ family: face.family, subfamily: face.subfamily, cmapCodepointSha256: face.cmapCodepointSha256, cmapCodepointCount: face.cmapCodepointCount }));
      exact(fields.sourceContainerDigest, source, "source-container-digests");
      exact(fields.extractedSfntDigest, extracted, "sfnt-digests");
      exact(fields.cmapDigest, cmap, "cmap-digests");
      yes(fields.ttcIndex === 0 && faces.some((face) => face.ttcIndex === 1), "ttc-index-observed");
      exact(fields.embeddedFontRelationship, inputOoxml.fontParts, "embedded-input-relations");
      exact(fields.inputEmbeddedPartDigest, inputOoxml.fontParts, "input-font-parts");
      exact(fields.savedEmbeddedPartDigest, savedOoxml.fontParts, "saved-font-parts");
      yes(Array.isArray(fields.savedEmbeddedPartDigest) && fields.savedEmbeddedPartDigest.length === 6 && uniqueStrings(fields.savedEmbeddedPartDigest.map((entry) => entry.partName)), "embedded-part-count-unique");
      break;
    }
    case "gminus1.paint.p-bg-roundtrip": {
      exact(first.background, reopen.background, "com-background-roundtrip");
      exact({ followMasterBackground: fields.followMasterBackground, fillType: fields.comFillType, fillRgb: fields.comFillRgb }, first.background, "com-background-binding");
      exact(fields.followMasterBackground, false, "follow-master");
      exact(fields.comFillType, 1, "solid-fill");
      yes(/^[0-9A-F]{6}$/.test(fields.comFillRgb ?? "") && fields.comFillRgb.match(/../g).reverse().join("") === fields.slideXmlSrgb, "com-ooxml-rgb");
      exact(fields.slideXmlPBgCount, 1, "p-bg-count");
      exact(fields.picturePlaneCount, 0, "picture-plane-count");
      exact(fields.effectsCount, 0, "effects-count");
      exact(inputOoxml.background, savedOoxml.background, "ooxml-background-roundtrip");
      exact(savedOoxml.background, { pBgCount: 1, srgb: fields.slideXmlSrgb, picturePlaneCount: 0, effectsCount: 0 }, "ooxml-background-shape");
      yes(stableRender(fields.firstReopenRenderMetricsFixedInteger), "render-equality");
      break;
    }
    case "gminus1.chart.line-style-roundtrip": {
      const firstChart = first.charts?.["case-chart-line"];
      const inputPart = mapByPart(inputOoxml.chartParts).get("ppt/charts/chart1.xml");
      const savedPart = mapByPart(savedOoxml.chartParts).get("ppt/charts/chart1.xml");
      yes(fields.chartType === "line-non-stacked-no-markers" && firstChart?.chartType === 4, "chart-type");
      yes(fields.seriesCount === 1 && firstChart?.seriesCount === 1, "series-count");
      exact(fields.seriesName, firstChart?.series?.[0]?.name, "series-name");
      exact(fields.seriesLineRgb, firstChart?.series?.[0]?.lineRgb, "line-style");
      exact(fields.titleText, firstChart?.titleText, "title");
      exact(fields.categories, inputPart?.categories, "category-cache");
      exact(fields.finiteValuesBinary64, inputPart?.values, "value-cache");
      yes(fields.categories?.length === fields.finiteValuesBinary64?.length && fields.categories.length > 0, "category-value-cardinality");
      yes(inputPart?.hasExternalData === false && sameCanonical(inputPart, savedPart), "ooxml-cache-roundtrip");
      yes(fields.firstReopenEquality === true && stableChart("case-chart-line"), "first-reopen-equality");
      yes(stableRender(fields.renderMetricsFixedInteger), "render-equality");
      break;
    }
    case "gminus1.chart.pie-point-style-roundtrip": {
      const firstChart = first.charts?.["case-chart-pie"];
      const inputPart = mapByPart(inputOoxml.chartParts).get("ppt/charts/chart2.xml");
      const savedPart = mapByPart(savedOoxml.chartParts).get("ppt/charts/chart2.xml");
      yes(fields.chartType === "pie-one-series-non-3d" && firstChart?.chartType === 5, "chart-type");
      yes(fields.seriesCount === 1 && firstChart?.seriesCount === 1, "series-count");
      exact(fields.categoryCount, inputPart?.categories?.length, "category-count");
      exact(fields.finiteValuesBinary64, inputPart?.values, "value-cache");
      exact(fields.pointIndexCategoryValueFillRgb, firstChart?.series?.[0]?.pointRgbs, "point-style");
      yes(fields.categoryCount > 0 && fields.categoryCount === fields.finiteValuesBinary64?.length && fields.categoryCount === fields.pointIndexCategoryValueFillRgb?.length, "category-value-style-cardinality");
      exact(fields.titleText, firstChart?.titleText, "title");
      yes(inputPart?.hasExternalData === false && sameCanonical(inputPart, savedPart), "ooxml-cache-roundtrip");
      yes(fields.firstReopenEquality === true && stableChart("case-chart-pie"), "first-reopen-equality");
      yes(stableRender(fields.renderMetricsFixedInteger), "render-equality");
      break;
    }
    case "gminus1.com.repair-dialog-witness": {
      const watcher = raw.watcher ?? {};
      const matching = (watcher.events ?? []).filter((event) => event.isPowerPointProcess === true && event.isDialog === false && event.processId === raw.firstOpenProcessId && event.hwnd === fields.powerPointHwnd);
      yes(Number.isSafeInteger(fields.powerPointHwnd) && fields.powerPointHwnd > 0 && matching.length === 1, "window-pid-witness");
      yes((watcher.events ?? []).filter((event) => event.isPowerPointProcess === true && event.isDialog === false && event.processId === raw.reopenProcessId && Number.isSafeInteger(event.hwnd) && event.hwnd > 0).length === 1, "reopen-window-pid-witness");
      yes(Array.isArray(watcher.repairDialogEvents) && watcher.repairDialogEvents.length === 0 && watcher.autoDismissAttempted === false, "repair-dialog-negative-witness");
      yes(fields.quarantineDecision === "accepted-no-repair-dialog-observed", "quarantine-decision");
      yes(sameCanonical(fields.observedDialogEvents, watcher) && sameCanonical(fields.attendedWitnessDigest, watcher), "watcher-binding");
      const times = [fields.dialogWatcherStartedAt, fields.openCallStartedAt, fields.openCallReturnedAt].map(Date.parse);
      yes(times.every(Number.isFinite) && times[0] <= times[1] && times[1] <= times[2], "open-timeline");
      break;
    }
    default:
      errors.push(`${caseId}:issuer-invariant-not-implemented`);
  }
  return errors;
}

export function evaluateGlobalInvariants(raw, evidence = {}) {
  const errors = [];
  const unicode = raw.unicodeExpectations ?? {};
  const exactUnicode = {
    rocketExpectedUtf16LeHex: "41003dd880de4200",
    rocketSourceUtf16LeHex: "41003dd880de4200",
    rocketEmittedUtf16LeHex: "41003dd880de4200",
    rocketFirstComUtf16LeHex: "41003dd880de4200",
    rocketReopenComUtf16LeHex: "41003dd880de4200",
    rocketPairUtf16LeHex: "3dd880de",
    nfcExpectedUtf16LeHex: "e900",
    nfcNormalizedUtf16LeHex: "e900",
    nfcFirstComUtf16LeHex: "e900",
    nfcReopenComUtf16LeHex: "e900",
    koreanExpectedUtf16LeHex: "5cd500ae200000ac98b0e4b2",
    koreanFirstComUtf16LeHex: "5cd500ae200000ac98b0e4b2",
    koreanReopenComUtf16LeHex: "5cd500ae200000ac98b0e4b2",
  };
  for (const [field, expected] of Object.entries(exactUnicode)) invariant(errors, unicode[field] === expected, `unicode-exact-mismatch:${field}`);
  const before = raw.processCensus?.before ?? {};
  const afterGrace = raw.processCensus?.afterGrace ?? {};
  const afterTimeout = raw.processCensus?.afterTimeout ?? {};
  invariant(errors, before.processCount === 0 && (before.unexpectedProcessIds ?? []).length === 0, "unexpected-powerpoint-before-open");
  invariant(errors, afterGrace.processCount === 0 && (afterGrace.unexpectedProcessIds ?? []).length === 0, "unexpected-powerpoint-after-grace");
  invariant(errors, afterTimeout.processCount === 0 && (afterTimeout.unexpectedProcessIds ?? []).length === 0, "powerpoint-survivor-after-timeout");
  invariant(errors, (raw.watcher?.repairDialogEvents ?? []).length === 0 && raw.watcher?.autoDismissAttempted === false, "repair-dialog-or-auto-dismiss");
  invariant(errors, raw.inputPptxDigest === evidence.inputDigest && raw.originalInputDigestAfterRun === evidence.inputDigest, "input-digest-chain");
  invariant(errors, raw.savedCopyPptxDigest === evidence.savedDigest, "saved-digest-chain");
  invariant(errors, raw.commonObservations?.inputPptxDigest === evidence.inputDigest && raw.commonObservations?.originalInputDigestAfterRun === evidence.inputDigest, "common-input-digest-chain");
  invariant(errors, raw.commonObservations?.savedCopyPptxDigest === evidence.savedDigest && raw.commonObservations?.reopenInputPptxDigest === evidence.savedDigest, "common-saved-digest-chain");
  invariant(errors, evidence.inputOoxml?.pptxDigest === evidence.inputDigest && evidence.savedOoxml?.pptxDigest === evidence.savedDigest, "ooxml-pptx-digest-chain");
  invariant(errors, evidence.first?.phase === "first-open" && evidence.reopen?.phase === "reopen", "com-phase-chain");
  invariant(errors, sameCanonical(raw.commonObservations?.firstOpenRenderArtifactDigests, raw.commonObservations?.reopenRenderArtifactDigests), "render-digest-roundtrip");
  invariant(errors, sameCanonical(raw.commonObservations?.rawArtifactRefs, raw.artifacts), "raw-artifact-ref-chain");
  const artifactDigests = evidence.artifactDigests ?? {};
  invariant(errors, raw.commonObservations?.inputOoxmlReadbackDigest === artifactDigests["input-ooxml"], "input-ooxml-artifact-chain");
  invariant(errors, raw.commonObservations?.savedOoxmlReadbackDigest === artifactDigests["saved-ooxml"], "saved-ooxml-artifact-chain");
  invariant(errors, raw.commonObservations?.firstOpenComObservationDigest === artifactDigests["first-open-com"], "first-com-artifact-chain");
  invariant(errors, raw.commonObservations?.reopenComObservationDigest === artifactDigests["reopen-com"], "reopen-com-artifact-chain");
  invariant(errors, raw.commonObservations?.powerPointProcessCensusBeforeOpen === artifactDigests["census-before"], "before-census-artifact-chain");
  invariant(errors, raw.commonObservations?.powerPointProcessCensusAfterCleanupGrace === artifactDigests["census-after-grace"], "grace-census-artifact-chain");
  invariant(errors, raw.commonObservations?.powerPointProcessCensusAfterCleanupTimeout === artifactDigests["census-after-timeout"], "timeout-census-artifact-chain");
  for (const [phaseField, artifactPrefix] of [["firstOpenRenderArtifactDigests", "render-first-open-slide-"], ["reopenRenderArtifactDigests", "render-reopen-slide-"]]) {
    invariant(errors, (raw.commonObservations?.[phaseField] ?? []).every((entry) => entry.sha256 === artifactDigests[`${artifactPrefix}${entry.slideNumber}`]), `${phaseField}-artifact-chain`);
  }
  const expectedSourceDigests = [evidence.inputDigest, evidence.savedDigest];
  invariant(errors, (raw.cases ?? []).every((entry) => sameCanonical(entry.sourceArtifactDigests, expectedSourceDigests)), "case-source-digest-chain");
  if (evidence.environmentDigest) {
    invariant(errors, raw.environmentReceiptDigest === evidence.environmentDigest && raw.commonObservations?.environmentReceiptDigest === evidence.environmentDigest, "environment-digest-chain");
  }
  return errors;
}

export function semanticSuiteStatus(errors, executedCaseCount = 18) {
  if (errors.length === 0 && executedCaseCount === 18) return "feasible";
  return errors.some((entry) => entry.startsWith("gminus1.")) ? "failed" : "blocked";
}

function captureCensus(options) {
  const census = captureBoundedSourceCensus(options.repoRoot);
  writeJson(options.output, census);
  process.stdout.write(`${JSON.stringify({ censusDigest: census.censusDigest, head: census.head, output: options.output })}\n`);
}

function qualify(options) {
  const manifest = readJson(options.manifest);
  const contract = readJson(options.validatorContract);
  const raw = readJson(options.rawReport);
  const seal = readJson(path.join(options.outputDir, "authority", "execution-seal.json"));
  const keyAttestation = readJson(path.join(options.outputDir, "authority", "signer-key-attestation.json"));
  const privateKey = crypto.createPrivateKey(fs.readFileSync(path.join(options.outputDir, "authority", ".ephemeral-private-key.pem")));
  const publicKey = crypto.createPublicKey({ key: Buffer.from(keyAttestation.publicKeySpkiBase64, "base64"), type: "spki", format: "der" });
  assert.equal(manifest.manifestDigest, digestProjected(manifest, ["manifestDigest"]));
  assert.equal(contract.contractDigest, digestProjected(contract, ["contractDigest"]), "validator-contract-digest");
  assert.equal(manifest.requiredCaseIdsDigest, manifestRequiredIdsDigest(manifest.cases), "manifest-case-id-digest");
  assert.equal(seal.manifestCoreDigest, digestProjected(manifest, ["executionPins", "manifestDigest"]));
  assert.equal(seal.executionSealDigest, digestProjected(seal, ["executionSealDigest", "executionSealSignatureBase64"]));
  assert.equal(crypto.verify(null, Buffer.from(seal.executionSealDigest, "ascii"), publicKey, Buffer.from(seal.executionSealSignatureBase64, "base64")), true);
  const pinnedByRole = new Map(seal.pinnedArtifacts.map((entry) => [entry.role, entry]));
  assert.equal(pinnedByRole.size, seal.pinnedArtifacts.length, "duplicate-pinned-role");
  for (const pinned of seal.pinnedArtifacts) {
    const pinnedPath = path.resolve(options.outputDir, pinned.locator);
    const actual = fileRecord(pinnedPath, pinned.artifactId, pinned.mediaType, pinned.locator);
    assert.equal(actual.sha256, pinned.sha256, `execution-pin-digest:${pinned.role}`);
    assert.equal(actual.byteLength, pinned.byteLength, `execution-pin-length:${pinned.role}`);
  }
  const censusPin = pinnedByRole.get("source-tree-census");
  assert.ok(censusPin, "source-tree-census-pin-missing");
  const openingCensusPath = path.resolve(options.outputDir, censusPin.locator);
  const openingCensus = readJson(openingCensusPath);
  assert.equal(openingCensus.schemaVersion, "SourceTreeCensusV1", "source-census-schema");
  assert.equal(openingCensus.censusDigest, seal.sourceTreeCensusDigest, "source-census-seal-pin");
  const closingCensus = captureBoundedSourceCensus(openingCensus.repositoryRoot);
  const closingCensusPath = path.join(options.outputDir, "authority", "source-tree-census-closing.json");
  writeJson(closingCensusPath, closingCensus);
  assertSourceTreeCensusUnchanged(openingCensus, closingCensus);
  assert.equal(raw.inputPptxDigest, seal.inputArtifactDigest, "raw/input pin mismatch");
  assert.equal(raw.originalInputDigestAfterRun, seal.inputArtifactDigest, "input mutated during probe");

  const ruleMap = observationRuleMap(contract);
  const artifactInventory = [];
  const artifactIds = new Set();
  function addArtifact(record) {
    if (artifactIds.has(record.artifactId)) throw new Error(`duplicate-artifact-id:${record.artifactId}`);
    artifactIds.add(record.artifactId);
    artifactInventory.push(record);
    return record;
  }
  addArtifact(fileRecord(openingCensusPath, censusPin.artifactId, censusPin.mediaType, censusPin.locator));
  addArtifact(fileRecord(closingCensusPath, "gate-minus1-source-tree-census-closing", "application/json", "authority/source-tree-census-closing.json"));
  for (const binding of raw.artifacts) {
    const filePath = path.resolve(options.outputDir, binding.locator);
    const record = fileRecord(filePath, binding.artifactId, binding.mediaType ?? mediaTypeFor(filePath), binding.locator);
    assert.equal(record.sha256, binding.sha256, `raw artifact digest mismatch:${binding.artifactId}`);
    addArtifact(record);
  }
  const sealRecord = fileRecord(path.join(options.outputDir, "authority", "execution-seal.json"), "gate-minus1-execution-seal", "application/json", "authority/execution-seal.json");
  if (!artifactIds.has(sealRecord.artifactId)) addArtifact(sealRecord);

  const bindingById = new Map(raw.artifacts.map((entry) => [entry.artifactId, entry]));
  const readBoundJson = (artifactId) => {
    const binding = bindingById.get(artifactId);
    assert.ok(binding, `raw-artifact-binding-missing:${artifactId}`);
    return readJson(path.resolve(options.outputDir, binding.locator));
  };
  const firstSnapshot = readBoundJson("first-open-com");
  const reopenSnapshot = readBoundJson("reopen-com");
  const inputOoxml = readBoundJson("input-ooxml");
  const savedOoxml = readBoundJson("saved-ooxml");
  const inputPptxPath = path.resolve(options.outputDir, bindingById.get("input-pptx").locator);
  const savedPptxPath = path.resolve(options.outputDir, bindingById.get("saved-pptx").locator);
  const inputZipParts = validateOoxmlReadbackFromPptx(inputPptxPath, inputOoxml);
  const savedZipParts = validateOoxmlReadbackFromPptx(savedPptxPath, savedOoxml);
  const inputSlide1Xml = inputZipParts.get("ppt/slides/slide1.xml")?.toString("utf8") ?? "";
  const savedSlide1Xml = savedZipParts.get("ppt/slides/slide1.xml")?.toString("utf8") ?? "";
  const environment = readJson(path.join(options.outputDir, "authority", "environment-receipt.json"));
  const fontInventoryPath = path.join(options.outputDir, "authority", "font-inventory.json");
  const fontInventory = readJson(fontInventoryPath);
  const carrier = readJson(path.join(options.outputDir, "authority", "carrier-values.json"));
  assert.equal(digestBytes(fs.readFileSync(fontInventoryPath)), environment.fontInventoryArtifactSha256, "font-inventory-artifact-digest");
  assert.equal(digestProjected(fontInventory, ["fontInventoryDigest"]), fontInventory.fontInventoryDigest, "font-inventory-self-digest");
  assert.equal(fontInventory.fontInventoryDigest, environment.fontInventoryDigest, "font-inventory-payload-digest");

  const expectedCases = manifest.cases.slice().sort((a, b) => a.ordinal - b.ordinal);
  const rawCaseIds = raw.cases.map((entry) => entry.caseId);
  const rawByCase = new Map(raw.cases.map((entry) => [entry.caseId, entry]));
  const orderedResults = [];
  const errors = [];
  if (rawByCase.size !== raw.cases.length) errors.push("duplicate-raw-case-id");
  const expectedCaseIdSet = new Set(expectedCases.map((entry) => entry.caseId));
  for (const rawCaseId of rawCaseIds) if (!expectedCaseIdSet.has(rawCaseId)) errors.push(`unexpected-raw-case:${rawCaseId}`);
  const inputArtifact = artifactInventory.find((entry) => entry.artifactId === "input-pptx");
  const savedArtifact = artifactInventory.find((entry) => entry.artifactId === "saved-pptx");
  const artifactDigests = Object.fromEntries(artifactInventory.map((entry) => [entry.artifactId, entry.sha256]));
  errors.push(...evaluateGlobalInvariants(raw, {
    inputDigest: inputArtifact?.sha256,
    savedDigest: savedArtifact?.sha256,
    inputOoxml,
    savedOoxml,
    first: firstSnapshot,
    reopen: reopenSnapshot,
    environmentDigest: environment.environmentReceiptDigest,
    artifactDigests,
  }));
  if (raw.environmentReceiptDigest !== manifest.requiredEnvironmentDigest || raw.environmentReceiptDigest !== seal.environmentReceiptDigest) errors.push("environment-manifest-seal-digest-chain");
  for (const caseContract of expectedCases) {
    const source = rawByCase.get(caseContract.caseId);
    if (!source) {
      errors.push(`missing-case:${caseContract.caseId}`);
      continue;
    }
    const expectedFields = caseContract.expectedObservationFields;
    const actualFields = Object.keys(source.fields);
    if (canonicalize(actualFields) !== canonicalize(expectedFields)) errors.push(`field-order-mismatch:${caseContract.caseId}`);
    const observations = [];
    const evidenceArtifactIds = [...source.evidenceArtifactIds];
    for (const fieldId of expectedFields) {
      const rule = ruleMap.get(fieldId);
      let value = source.fields[fieldId];
      if (rule?.valueType === "digest" && !DIGEST_RE.test(value)) {
        const complexArtifact = artifactFromObject(options.outputDir, `${caseContract.caseId}.${fieldId}`, { schemaVersion: "gate-minus1-complex-observation/v1", caseId: caseContract.caseId, fieldId, value }, `observations/${String(caseContract.ordinal).padStart(2, "0")}-${fieldId}.json`);
        addArtifact(complexArtifact);
        evidenceArtifactIds.push(complexArtifact.artifactId);
        value = complexArtifact.sha256;
      }
      validateObservationValue(fieldId, value, rule);
      observations.push({ fieldId, ...rule, value });
    }
    const requiredSchemas = contract.structuredCaseContracts[caseContract.caseId].requiredSchemas;
    const payloadRecords = [];
    for (const [schemaIndex, schemaVersion] of requiredSchemas.entries()) {
      const payload = {
        schemaVersion,
        caseId: caseContract.caseId,
        collectorId: source.collectorId,
        collectorSourceDigest: raw.probeScriptDigest,
        environmentReceiptDigest: raw.environmentReceiptDigest,
        collectedAt: source.collectedAt,
        artifactBinding: { artifactId: `${caseContract.caseId}.structured-payload.${schemaIndex + 1}`, versionId: `attempt-${raw.attemptId}`, mediaType: "application/json", byteLength: 1 },
        sourceArtifactDigests: source.sourceArtifactDigests,
        fieldIds: expectedFields,
        observations,
      };
      let payloadBytes = Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8");
      payload.artifactBinding.byteLength = payloadBytes.length;
      payloadBytes = Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8");
      payload.artifactBinding.byteLength = payloadBytes.length;
      const payloadPath = path.join(options.outputDir, "observations", `${String(caseContract.ordinal).padStart(2, "0")}-structured-${schemaIndex + 1}.json`);
      fs.writeFileSync(payloadPath, payloadBytes, { flag: "wx" });
      const payloadRecord = fileRecord(payloadPath, payload.artifactBinding.artifactId, "application/json", path.relative(options.outputDir, payloadPath).replaceAll("\\", "/"));
      addArtifact(payloadRecord);
      evidenceArtifactIds.push(payloadRecord.artifactId);
      payloadRecords.push(payloadRecord);
    }
    const invariantErrors = evaluateCaseInvariant(caseContract.caseId, source.fields, {
      first: firstSnapshot,
      reopen: reopenSnapshot,
      inputOoxml,
      savedOoxml,
      fontInventory,
      raw,
      carrier,
      inputSlide1Xml,
      savedSlide1Xml,
    });
    errors.push(...invariantErrors);
    const assertionState = invariantErrors.length === 0 ? "satisfied" : "failed";
    const disposition = invariantErrors.length === 0 ? expectedDisposition(caseContract.caseId) : "failed";
    if (!caseContract.allowedDispositions.includes(disposition)) errors.push(`issuer-disallowed-disposition:${caseContract.caseId}:${disposition}`);
    orderedResults.push({
      ordinal: caseContract.ordinal,
      caseId: caseContract.caseId,
      disposition,
      assertionState,
      observedFieldIds: expectedFields,
      missingObservationFieldIds: [],
      observations,
      evidenceArtifactIds: [...new Set(evidenceArtifactIds)],
      observationDigest: payloadRecords[0].sha256,
    });
  }

  const commonObservations = manifest.requiredCommonObservationFields.map((fieldId) => {
    const rule = ruleMap.get(fieldId);
    let value = raw.commonObservations[fieldId];
    if (rule?.valueType === "digest" && !DIGEST_RE.test(value)) {
      const record = artifactFromObject(options.outputDir, `common.${fieldId}`, { schemaVersion: "gate-minus1-common-observation/v1", fieldId, value }, `common/${fieldId}.json`);
      addArtifact(record);
      value = record.sha256;
    }
    validateObservationValue(fieldId, value, rule);
    return { fieldId, ...rule, value };
  });

  const before = raw.processCensus.before;
  const afterGrace = raw.processCensus.afterGrace;
  const afterTimeout = raw.processCensus.afterTimeout;
  if (before.unexpectedProcessIds.length !== 0) errors.push("unexpected-powerpoint-before-open");
  if (afterGrace.unexpectedProcessIds.length !== 0 || afterTimeout.unexpectedProcessIds.length !== 0) errors.push("unexpected-powerpoint-after-cleanup");
  if (afterTimeout.processCount !== 0) errors.push("powerpoint-survivor-after-timeout");
  if ((raw.watcher.repairDialogEvents ?? []).length !== 0) errors.push("repair-dialog-observed");
  const repairCase = rawByCase.get("gminus1.com.repair-dialog-witness");
  const observedHwnd = repairCase?.fields?.powerPointHwnd;
  const matchingWindowEvent = (raw.watcher.events ?? []).some((event) =>
    event.isPowerPointProcess === true && event.isDialog === false &&
    event.processId === raw.firstOpenProcessId && event.hwnd === observedHwnd
  );
  if (!Number.isSafeInteger(observedHwnd) || observedHwnd <= 0 || !matchingWindowEvent) {
    errors.push("powerpoint-hwnd-witness-missing-or-pid-mismatch");
  }

  if (orderedResults.some((entry) => entry.evidenceArtifactIds.some((id) => !artifactIds.has(id)))) errors.push("unresolved-evidence-artifact-id");

  artifactInventory.sort((a, b) => a.artifactId.localeCompare(b.artifactId) || a.versionId.localeCompare(b.versionId));
  const ids = orderedResults.map((entry) => entry.caseId);
  const expectedIds = expectedCases.map((entry) => entry.caseId);
  const duplicateCaseIds = [...new Set(rawCaseIds.filter((id, index) => rawCaseIds.indexOf(id) !== index))];
  const missingCaseIds = expectedIds.filter((id) => !rawCaseIds.includes(id));
  const unexpectedCaseIds = [...new Set(rawCaseIds.filter((id) => !expectedCaseIdSet.has(id)))];
  const blockedCaseCount = orderedResults.filter((entry) => entry.assertionState === "blocked").length;
  const failedCaseCount = orderedResults.filter((entry) => entry.assertionState === "failed").length;
  const notVerifiedCaseCount = orderedResults.filter((entry) => entry.assertionState === "not-verified").length;
  const suiteStatus = semanticSuiteStatus(errors, orderedResults.length);
  const executionSealArtifactRef = manifest.executionPins;
  const result = {
    schemaVersion: "authored-gate-minus1-result-set/v1",
    manifestDigest: manifest.manifestDigest,
    requiredCaseIdsDigest: manifest.requiredCaseIdsDigest,
    semanticValidatorContractDigest: contract.contractDigest,
    suiteStatus,
    requiredCaseCount: 18,
    executedCaseCount: orderedResults.length,
    blockedCaseCount,
    failedCaseCount,
    notVerifiedCaseCount,
    skippedCaseCount: 0,
    missingCaseIds,
    duplicateCaseIds,
    unexpectedCaseIds,
    orderedResults,
    commonObservations,
    environmentReceiptDigest: raw.environmentReceiptDigest,
    executionSealArtifactRef,
    artifactInventory,
    artifactInventoryDigestProjection: { canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$.artifactInventory", ordering: "artifactId then versionId", excludedJsonPaths: [] },
    artifactInventoryDigest: digestObject(artifactInventory),
    semanticValidation: {
      orderedCaseIdSetExact: canonicalize(ids) === canonicalize(expectedIds),
      countEquationExact: orderedResults.length + missingCaseIds.length === 18,
      noBlockedFailedOrNotVerifiedDisposition: errors.length === 0,
      observationFieldSetsExact: !errors.some((entry) => entry.startsWith("field-order-mismatch")),
      observationValuesTypedIntegerOnly: true,
      allEvidenceArtifactRefsResolve: orderedResults.every((entry) => entry.evidenceArtifactIds.every((id) => artifactIds.has(id))),
      artifactIdsUnique: artifactIds.size === artifactInventory.length,
      environmentDigestExact: raw.environmentReceiptDigest === manifest.requiredEnvironmentDigest,
      executionSealArtifactRefExact: digestObject(executionSealArtifactRef) === digestObject(manifest.executionPins),
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
  result.resultCoreDigest = digestProjected(result, ["resultCoreDigest", "semanticValidationReceiptRef", "resultSetDigest"]);
  const receiptIssuedAt = rfc3339();
  const receiptPayload = {
    receiptSchemaVersion: "authored-semantic-validation-receipt/v1",
    issuerId: "gate-minus1-actual-probe/local-qualification-issuer-v1",
    signerKeyId: keyAttestation.signerKeyId,
    signatureAlgorithm: "Ed25519",
    signatureBase64: "",
    issuedAt: receiptIssuedAt,
    expiresAt: rfc3339(new Date(Date.now() + 30 * 60 * 1000)),
    nonce: crypto.randomBytes(24).toString("hex"),
    subjectManifestDigest: manifest.manifestDigest,
    subjectResultCoreDigest: result.resultCoreDigest,
    semanticValidatorContractDigest: contract.contractDigest,
    receiptDigest: "",
  };
  receiptPayload.receiptDigest = digestProjected(receiptPayload, ["receiptDigest", "signatureBase64"]);
  receiptPayload.signatureBase64 = crypto.sign(null, Buffer.from(receiptPayload.receiptDigest, "ascii"), privateKey).toString("base64");
  assert.equal(crypto.verify(null, Buffer.from(receiptPayload.receiptDigest, "ascii"), publicKey, Buffer.from(receiptPayload.signatureBase64, "base64")), true);
  const receiptPath = path.join(options.outputDir, "authority", "semantic-validation-receipt.json");
  writeJson(receiptPath, receiptPayload);
  const receiptRecord = fileRecord(receiptPath, "gate-minus1-semantic-validation-receipt", "application/json", "authority/semantic-validation-receipt.json");
  result.semanticValidationReceiptRef = { ...receiptRecord, ...receiptPayload };
  delete result.semanticValidationReceiptRef.retentionClass;
  delete result.semanticValidationReceiptRef.locator;
  result.resultSetDigest = digestProjected(result, ["resultSetDigest"]);
  const resultPath = path.join(options.outputDir, "gate-minus1-result-set.json");
  writeJson(resultPath, result);
  const summary = { suiteStatus, executedCaseCount: result.executedCaseCount, skippedCaseCount: 0, errors, manifestDigest: manifest.manifestDigest, sourceTreeCensusDigest: openingCensus.censusDigest, sourceHeadCommit: openingCensus.head, ignoredAllowlistPolicyDigest: openingCensus.ignoredAllowlistPolicyDigest, generatedCachePolicyDigest: openingCensus.generatedCachePolicyDigest, resultCoreDigest: result.resultCoreDigest, resultSetDigest: result.resultSetDigest, resultPath };
  writeJson(path.join(options.outputDir, "gate-minus1-summary.json"), summary);
  process.stdout.write(`${JSON.stringify(summary)}\n`);
  if (suiteStatus !== "feasible") process.exitCode = 2;
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

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)))) {
  const { command, options } = parseArgs(process.argv.slice(2));
  if (command === "census") captureCensus(options);
  else if (command === "prepare") prepare(options);
  else if (command === "qualify") qualify(options);
  else throw new Error("usage: gate-minus1-authority.mjs census|prepare|qualify --...");
}
