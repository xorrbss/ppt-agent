#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { canonicalJson, canonicalSha256, bytesSha256 } from "../contracts/authored_vnext/semantic/fixtures/canonical-json.mjs";

const DEFAULT_FIXTURE = path.resolve("contracts/authored_vnext/semantic/fixtures/vertical-slice-fixture.json");
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const SCHEMA = "authored-semantic-atomic-evidence/v2";
const SLIDE_COUNT = 8;
const REQUIRED_SCENARIOS = ["table", "native-chart", "diagram-shapes", "cropped-image", "long-korean", "multi-column", "mixed-css", "boundary-sentinels"];

function fail(message) {
  throw new Error(message);
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    fail(`invalid-json:${file}:${error.message}`);
  }
}

function requireString(value, label) {
  if (typeof value !== "string" || value.length === 0) fail(`missing-string:${label}`);
  return value;
}

function digestBytes(value, label) {
  const digest = bytesSha256(Buffer.from(value, "utf8"));
  if (!SHA256.test(digest)) fail(`invalid-digest:${label}`);
  return digest;
}

function unique(values, label) {
  if (new Set(values).size !== values.length) fail(`duplicate:${label}`);
}

function deriveBundle(slide, fixture) {
  const qids = [...slide.html.matchAll(/data-qid=['"]([^'"]+)['"]/g)].map((match) => match[1]);
  unique(qids, `${slide.slideId}:html-qid`);
  const elementIds = slide.semanticElements.map((element) => requireString(element.elementId, `${slide.slideId}:elementId`));
  unique(elementIds, `${slide.slideId}:semantic-qid`);
  if (qids.join("\u0000") !== elementIds.join("\u0000")) fail(`qid-mismatch:${slide.slideId}`);

  const htmlDigest = digestBytes(slide.html, `${slide.slideId}:html`);
  const semanticDigest = canonicalSha256({ schemaVersion: "authored-semantic-source/v1", elements: slide.semanticElements });
  const assets = slide.assets.map((asset) => ({
    assetId: requireString(asset.assetId, `${slide.slideId}:assetId`),
    mediaType: requireString(asset.mediaType, `${slide.slideId}:mediaType`),
    locatorDigest: digestBytes(requireString(asset.locator, `${slide.slideId}:asset-locator`), `${slide.slideId}:asset-locator`),
    digest: digestBytes(requireString(asset.bytes, `${slide.slideId}:asset-bytes`), `${slide.slideId}:asset`),
  }));
  const fonts = slide.fonts.map((font) => ({
    faceId: requireString(font.faceId, `${slide.slideId}:faceId`),
    family: requireString(font.family, `${slide.slideId}:font-family`),
    locatorDigest: digestBytes(requireString(font.locator, `${slide.slideId}:font-locator`), `${slide.slideId}:font-locator`),
    digest: digestBytes(requireString(font.bytes, `${slide.slideId}:font-bytes`), `${slide.slideId}:font`),
  }));
  unique(assets.map((asset) => asset.assetId), `${slide.slideId}:asset-id`);
  unique(fonts.map((font) => font.faceId), `${slide.slideId}:font-id`);
  const assetManifestDigest = canonicalSha256(assets);
  const fontManifestDigest = canonicalSha256(fonts);
  const registryDigest = canonicalSha256({ qids: elementIds });
  const bundleIdentity = {
    schemaVersion: "authored-emission-bundle/v1",
    bundleKind: "authored",
    presentationId: fixture.presentationId,
    slideId: slide.slideId,
    slideRevision: slide.slideRevision,
    operationId: fixture.operationId,
    originGenerationAttemptId: fixture.generationAttemptId,
    htmlDigest,
    semanticDigest,
    assetManifestDigest,
    fontManifestDigest,
    masterContractDigest: digestBytes(fixture.masterContractBytes, "master"),
    compositionDigest: digestBytes(fixture.compositionBytes, "composition"),
    elementIdentityRegistryDigest: registryDigest,
    qualityPolicyVersion: fixture.qualityPolicyVersion,
  };
  return { slide, qids: elementIds, assets, fonts, bundleIdentity, bundleDigest: canonicalSha256(bundleIdentity) };
}

function deriveFixture(fixture) {
  requireString(fixture.presentationId, "presentationId");
  requireString(fixture.operationId, "operationId");
  requireString(fixture.generationAttemptId, "generationAttemptId");
  if (fixture.schemaVersion !== "authored-semantic-vertical-slice-fixture/v2") fail("fixture-schema-unsupported");
  if (!Array.isArray(fixture.slides) || fixture.slides.length !== SLIDE_COUNT) fail("fixture-must-have-eight-slides");
  if (JSON.stringify(fixture.scenarios) !== JSON.stringify(REQUIRED_SCENARIOS)) fail("fixture-scenario-order-mismatch");
  unique(fixture.slides.map((slide) => slide.slideId), "slide-id");
  if (fixture.slides.some((slide) => slide.genericCssClass !== fixture.genericCssClass)) fail("generic-class-isolation-missing");
  unique(fixture.slides.map((slide) => slide.scopeId), "slide-scope-id");
  if (fixture.slides.some((slide) => !slide.html.includes(`data-scope='${slide.scopeId}'`) || !slide.html.includes(`class='${fixture.genericCssClass}'`))) fail("slide-scope-css-binding-missing");
  if (new Set(fixture.slides.map((slide) => slide.scenario)).size !== SLIDE_COUNT) fail("slide-scenario-duplicate");
  for (const scenario of REQUIRED_SCENARIOS) if (!fixture.slides.some((slide) => slide.scenario === scenario)) fail(`scenario-missing:${scenario}`);
  if (new Set(fixture.slides.map((slide) => slide.html)).size !== SLIDE_COUNT) fail("slide-html-must-differ");
  const bundles = fixture.slides.map((slide) => deriveBundle(slide, fixture));
  for (const bundle of bundles) {
    for (const sentinel of bundle.slide.sentinels) {
      if (!bundle.qids.includes(sentinel)) fail(`sentinel-missing:${bundle.slide.slideId}:${sentinel}`);
    }
  }
  const mergedAssets = bundles.flatMap((bundle) => bundle.assets);
  const mergedFonts = bundles.flatMap((bundle) => bundle.fonts);
  const byId = new Map();
  for (const item of mergedAssets) {
    const previous = byId.get(`asset:${item.assetId}`);
    if (previous && previous.digest !== item.digest) fail(`asset-digest-conflict:${item.assetId}`);
    byId.set(`asset:${item.assetId}`, item);
  }
  for (const item of mergedFonts) {
    const previous = byId.get(`font:${item.faceId}`);
    if (previous && previous.digest !== item.digest) fail(`font-digest-conflict:${item.faceId}`);
    byId.set(`font:${item.faceId}`, item);
  }
  const orderedSlides = bundles.map((bundle) => ({ slideId: bundle.slide.slideId, slideRevision: bundle.slide.slideRevision, bundleKind: "authored", bundleDigest: bundle.bundleDigest }));
  const manifestIdentity = {
    schemaVersion: "authored-deck-emission-manifest/v1",
    presentationId: fixture.presentationId,
    presentationRevision: fixture.presentationRevision,
    operationId: fixture.operationId,
    generationAttemptId: fixture.generationAttemptId,
    orderedSlides,
    masterContractDigest: bundles[0].bundleIdentity.masterContractDigest,
    compositionDigest: bundles[0].bundleIdentity.compositionDigest,
    assetManifestDigest: canonicalSha256(mergedAssets.sort((a, b) => a.assetId.localeCompare(b.assetId))),
    fontManifestDigest: canonicalSha256(mergedFonts.sort((a, b) => a.faceId.localeCompare(b.faceId))),
    qualityPolicyVersion: fixture.qualityPolicyVersion,
  };
  return { fixture, bundles, manifestIdentity, canonicalDigest: canonicalSha256(manifestIdentity) };
}

function verifyEvidence(derived, evidencePath) {
  const evidenceRoot = path.dirname(path.resolve(evidencePath));
  const evidence = readJson(evidencePath);
  if (evidence.schemaVersion !== SCHEMA) fail("evidence-schema-unsupported");
  for (const key of ["fixtureId", "presentationId", "operationId", "generationAttemptId"]) {
    if (evidence[key] !== derived.fixture[key]) fail(`evidence-binding-mismatch:${key}`);
  }
  if (evidence.presentationRevision !== derived.fixture.presentationRevision || evidence.canonicalDigest !== derived.canonicalDigest) fail("evidence-binding-mismatch:revision-or-canonical");
  for (const key of ["promotionReady", "fullNativeParityReady", "independentVnextGenerationReady"]) {
    if (evidence[key] !== false) fail(`promotion-flag-not-false:${key}`);
  }
  if (!Array.isArray(evidence.modes) || evidence.modes.length !== 2) fail("evidence-requires-fidelity-and-hybrid");
  const modes = new Set();
  const attempts = new Set();
  const artifactPaths = new Set();
  for (const mode of evidence.modes) {
    if (!["fidelity", "hybrid"].includes(mode.mode)) fail(`unsupported-mode:${mode.mode}`);
    if (modes.has(mode.mode)) fail(`duplicate-mode:${mode.mode}`);
    modes.add(mode.mode);
    requireString(mode.attemptId, `${mode.mode}:attemptId`);
    if (attempts.has(mode.attemptId)) fail("attempt-reused");
    attempts.add(mode.attemptId);
    if (mode.presentationRevision !== derived.fixture.presentationRevision || mode.canonicalDigest !== derived.canonicalDigest) fail(`mode-binding-mismatch:${mode.mode}`);
    const expectedRendererDigest = canonicalSha256({ canonicalDigest: derived.canonicalDigest, mode: mode.mode, attemptId: mode.attemptId });
    if (mode.rendererSourceDigest !== expectedRendererDigest) fail(`renderer-digest-mismatch:${mode.mode}`);
    if (mode.visualParity === "passed" || mode.status === "passed") fail(`unverified-evidence-claimed-passed:${mode.mode}`);
    if (!Array.isArray(mode.artifacts) || mode.artifacts.length < 3) fail(`artifacts-incomplete:${mode.mode}`);
    for (const artifact of mode.artifacts) {
      const relative = requireString(artifact.path, `${mode.mode}:artifact-path`);
      const resolved = path.resolve(evidenceRoot, relative);
      if (resolved !== evidenceRoot && !resolved.startsWith(`${evidenceRoot}${path.sep}`)) fail("artifact-path-escapes-evidence-root");
      if (artifactPaths.has(resolved)) fail("artifact-reused-across-modes");
      artifactPaths.add(resolved);
      if (!fs.existsSync(resolved)) fail(`artifact-missing:${relative}`);
      const actual = bytesSha256(fs.readFileSync(resolved));
      if (artifact.sha256 !== actual) fail(`artifact-digest-mismatch:${relative}`);
    }
    if (!Array.isArray(mode.slides) || mode.slides.length !== SLIDE_COUNT) fail(`slide-evidence-incomplete:${mode.mode}`);
  }
  if (!modes.has("fidelity") || !modes.has("hybrid")) fail("fidelity-hybrid-pair-missing");
  return { status: "not-verified", evidencePath };
}

export function verifyFixture(fixturePath = DEFAULT_FIXTURE, evidencePath = null) {
  const derived = deriveFixture(readJson(fixturePath));
  const result = { status: "not-verified", fixturePath, fixtureId: derived.fixture.fixtureId, presentationId: derived.fixture.presentationId, presentationRevision: derived.fixture.presentationRevision, operationId: derived.fixture.operationId, generationAttemptId: derived.fixture.generationAttemptId, canonicalDigest: derived.canonicalDigest, bundleDigests: derived.bundles.map((bundle) => bundle.bundleDigest), promotionReady: false, fullNativeParityReady: false, independentVnextGenerationReady: false };
  if (evidencePath) Object.assign(result, verifyEvidence(derived, evidencePath));
  return result;
}

if (process.argv[1] && path.resolve(fileURLToPath(import.meta.url)) === path.resolve(process.argv[1])) {
  const args = process.argv.slice(2);
  const fixtureIndex = args.indexOf("--fixture");
  const evidenceIndex = args.indexOf("--evidence");
  const fixturePath = fixtureIndex >= 0 ? path.resolve(args[fixtureIndex + 1]) : DEFAULT_FIXTURE;
  const evidencePath = evidenceIndex >= 0 ? path.resolve(args[evidenceIndex + 1]) : null;
  try {
    console.log(JSON.stringify(verifyFixture(fixturePath, evidencePath), null, 2));
  } catch (error) {
    console.error(`authored-semantic-fixture-invalid:${error.message}`);
    process.exitCode = 1;
  }
}
