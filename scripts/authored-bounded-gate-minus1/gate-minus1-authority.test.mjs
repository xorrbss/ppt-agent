import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalize,
  digestObject,
  evaluateCaseInvariant,
  evaluateGlobalInvariants,
  qidCarrier,
  semanticSuiteStatus,
} from "./gate-minus1-authority.mjs";

test("canonical JSON rejects fractional and negative zero values", () => {
  assert.throws(() => canonicalize({ value: 1.5 }), /non-canonical-number/);
  assert.throws(() => canonicalize({ value: -0 }), /non-canonical-number/);
});

test("canonical JSON normalizes strings and orders keys by UTF-8 bytes", () => {
  assert.equal(canonicalize({ z: "e\u0301", a: 1 }), '{"a":1,"z":"é"}');
  assert.equal(digestObject({ b: 2, a: 1 }), digestObject({ a: 1, b: 2 }));
});

test("QID carriers are uppercase RFC4648 base32 without padding", () => {
  const carrier = qidCarrier("Q", "slide-1:text:owner_01");
  assert.match(carrier, /^Q2_[A-Z2-7]+$/);
  assert.ok(!carrier.includes("="));
});

test("issuer rejects a first/reopen self-declaration mutation", () => {
  const shape = { utf16LeHex: "e900", length: 1 };
  const fields = {
    sourceUtf16LeHex: "65000103",
    normalizedNfcUtf16LeHex: "e900",
    comTextUtf16LeHex: "e900",
    charactersStartLengthText: [{ request: 1, start: 1, length: 1, text: "é" }],
    firstReopenEquality: false,
  };
  const errors = evaluateCaseInvariant("gminus1.text.combining-nfc", fields, { first: { texts: { "case-combining-nfc": shape } }, reopen: { texts: { "case-combining-nfc": structuredClone(shape) } } });
  assert.match(errors.join("\n"), /first-reopen-equality/);
  assert.notEqual(semanticSuiteStatus(errors), "feasible");
});

test("issuer rejects duplicate QID even when the probe claims equality", () => {
  const qid = qidCarrier("Q", "same-owner");
  const owner = (suffix) => ({ PRESENTON_QID_V2: qid, PRESENTON_KIND_V2: qidCarrier("K", "chart"), PRESENTON_OWNER_ROLE_V2: qidCarrier("R", suffix) });
  const comTagMap = { overflow: owner("a"), line: owner("b"), pie: owner("c") };
  const fields = { displayName: "owners", comTagMap, ooxmlTagRelationshipGraph: [], decodedSemanticTagMap: structuredClone(comTagMap), duplicateOrOrphanCount: 0, firstReopenEquality: true };
  const evidence = { first: { ownerTags: structuredClone(comTagMap) }, reopen: { ownerTags: structuredClone(comTagMap) }, inputOoxml: { orphanTagPartCount: 0 }, savedOoxml: { orphanTagPartCount: 0 } };
  const errors = evaluateCaseInvariant("gminus1.qid.shape-tags-roundtrip", fields, evidence);
  assert.match(errors.join("\n"), /qid-unique-uppercase/);
  assert.notEqual(semanticSuiteStatus(errors), "feasible");
});

test("issuer rejects chart cache mismatch independent of raw assertion state", () => {
  const metrics = { dimensions: { width: 1280, height: 720 }, firstOpen: [{ slideNumber: 3, sha256: `sha256:${"1".repeat(64)}` }], reopen: [{ slideNumber: 3, sha256: `sha256:${"1".repeat(64)}` }] };
  const chart = { chartType: 4, seriesCount: 1, titleText: "분기 추세", series: [{ name: "매출", lineRgb: "C07000", pointRgbs: null }] };
  const fields = { chartType: "line-non-stacked-no-markers", seriesCount: 1, seriesName: "매출", categories: ["1분기"], finiteValuesBinary64: ["4.3"], seriesLineRgb: "C07000", titleText: "분기 추세", firstReopenEquality: true, renderMetricsFixedInteger: metrics };
  const inputPart = { partName: "ppt/charts/chart1.xml", hasExternalData: false, categories: ["1분기"], values: ["4.3"], xmlSha256: `sha256:${"2".repeat(64)}` };
  const savedPart = { ...structuredClone(inputPart), values: ["9.9"] };
  const evidence = { first: { charts: { "case-chart-line": chart } }, reopen: { charts: { "case-chart-line": structuredClone(chart) } }, inputOoxml: { chartParts: [inputPart] }, savedOoxml: { chartParts: [savedPart] } };
  const errors = evaluateCaseInvariant("gminus1.chart.line-style-roundtrip", fields, evidence);
  assert.match(errors.join("\n"), /ooxml-cache-roundtrip/);
  assert.notEqual(semanticSuiteStatus(errors), "feasible");
});

test("issuer rejects a surviving POWERPNT process", () => {
  const digest = `sha256:${"a".repeat(64)}`;
  const artifactDigests = {
    "input-ooxml": digest, "saved-ooxml": digest, "first-open-com": digest, "reopen-com": digest,
    "census-before": digest, "census-after-grace": digest, "census-after-timeout": digest,
    "render-first-open-slide-1": digest, "render-reopen-slide-1": digest,
  };
  const unicodeExpectations = {
    rocketExpectedUtf16LeHex: "41003dd880de4200", rocketSourceUtf16LeHex: "41003dd880de4200", rocketEmittedUtf16LeHex: "41003dd880de4200", rocketFirstComUtf16LeHex: "41003dd880de4200", rocketReopenComUtf16LeHex: "41003dd880de4200", rocketPairUtf16LeHex: "3dd880de",
    nfcExpectedUtf16LeHex: "e900", nfcNormalizedUtf16LeHex: "e900", nfcFirstComUtf16LeHex: "e900", nfcReopenComUtf16LeHex: "e900",
    koreanExpectedUtf16LeHex: "5cd500ae200000ac98b0e4b2", koreanFirstComUtf16LeHex: "5cd500ae200000ac98b0e4b2", koreanReopenComUtf16LeHex: "5cd500ae200000ac98b0e4b2",
  };
  const artifacts = [{ artifactId: "only", sha256: digest }];
  const common = {
    inputPptxDigest: digest, originalInputDigestAfterRun: digest, savedCopyPptxDigest: digest, reopenInputPptxDigest: digest,
    inputOoxmlReadbackDigest: digest, savedOoxmlReadbackDigest: digest, firstOpenComObservationDigest: digest, reopenComObservationDigest: digest,
    powerPointProcessCensusBeforeOpen: digest, powerPointProcessCensusAfterCleanupGrace: digest, powerPointProcessCensusAfterCleanupTimeout: digest,
    firstOpenRenderArtifactDigests: [{ slideNumber: 1, sha256: digest }], reopenRenderArtifactDigests: [{ slideNumber: 1, sha256: digest }],
    rawArtifactRefs: artifacts, environmentReceiptDigest: digest,
  };
  const raw = {
    unicodeExpectations, inputPptxDigest: digest, originalInputDigestAfterRun: digest, savedCopyPptxDigest: digest, environmentReceiptDigest: digest,
    processCensus: { before: { processCount: 0, unexpectedProcessIds: [] }, afterGrace: { processCount: 0, unexpectedProcessIds: [] }, afterTimeout: { processCount: 1, unexpectedProcessIds: [4242] } },
    watcher: { repairDialogEvents: [], autoDismissAttempted: false }, commonObservations: common, artifacts, cases: [{ sourceArtifactDigests: [digest, digest] }],
  };
  const errors = evaluateGlobalInvariants(raw, { inputDigest: digest, savedDigest: digest, inputOoxml: { pptxDigest: digest }, savedOoxml: { pptxDigest: digest }, first: { phase: "first-open" }, reopen: { phase: "reopen" }, environmentDigest: digest, artifactDigests });
  assert.match(errors.join("\n"), /powerpoint-survivor-after-timeout/);
  assert.notEqual(semanticSuiteStatus(errors), "feasible");
});
