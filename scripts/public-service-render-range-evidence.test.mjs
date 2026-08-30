import assert from "node:assert/strict";
import test from "node:test";

import {
  computeRenderRangeBundleDigest,
  REQUIRED_RANGE_SLIDE_COUNT,
  validateRenderRangeEvidence,
} from "./public-service-render-range-evidence.mjs";

const RELEASE_CANDIDATE = `sha256:${"a".repeat(64)}`;
const PPTX_DIGEST = `sha256:${"b".repeat(64)}`;

function passingReport() {
  const powerPointImages = [];
  const libreOfficeImages = [];
  const comparisons = [];
  const slides = [];
  for (let slideNumber = 1; slideNumber <= REQUIRED_RANGE_SLIDE_COUNT; slideNumber += 1) {
    const ppDigest = `sha256:${slideNumber.toString(16).padStart(64, "0")}`;
    const loDigest = `sha256:${(slideNumber + 100).toString(16).padStart(64, "0")}`;
    powerPointImages.push({ renderer: "powerpoint", slideNumber, sha256: ppDigest,
      bytes: 10_000 + slideNumber, widthPx: 1600, heightPx: 900 });
    libreOfficeImages.push({ renderer: "libreoffice", slideNumber, sha256: loDigest,
      bytes: 11_000 + slideNumber, widthPx: 1600, heightPx: 900 });
    comparisons.push({ slideNumber, powerPointImageSha256: ppDigest,
      libreOfficeImageSha256: loDigest, widthPx: 1600, heightPx: 900,
      metrics: { changedPixelRatioPpm: 2_000, block8LumaSsimMillionths: 990_000,
        meanAbsoluteChannelDeltaMilli: 300, rmseMilli: 400 }, thresholdPassed: true });
    slides.push({ slideNumber, visibleTextRunCount: 5, measuredTextRunCount: 5,
      domRangeRectCount: 6, nonFiniteRectCount: 0, zeroAreaRectCount: 0,
      unmeasuredVisibleTextRunCount: 0, outOfBoundsRectCount: 0, clippedTextRunCount: 0 });
  }
  const report = {
    schemaVersion: "public-service-render-range-evidence/v1",
    releaseCandidate: RELEASE_CANDIDATE,
    sourceTreeCommit: "c".repeat(40),
    evidenceClass: "actual_product_path",
    provenance: { runnerPath: "scripts/public-service-render-range-evidence.mjs",
      runnerSha256: `sha256:${"d".repeat(64)}`, sourceManifestDigest: RELEASE_CANDIDATE },
    deck: { deckId: "range-30", operationId: "operation-30", presentationId: "presentation-30",
      slideCount: 30, artifact: { mediaType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        slideCount: 30, bytes: 500_000, sha256: PPTX_DIGEST } },
    renderers: {
      powerpoint: { available: true, version: "16.0", ownedProcessBindingExact: true,
        reopened: true, readbackPassed: true, firstOpenSaveCopyReopenReadbackPassed: true,
        firstOpenReopenedRenderHashesMatch: true, repairWarningObserved: false,
        unexpectedProcessCount: 0, finalProcessCount: 0, inputSha256: PPTX_DIGEST,
        inputSha256After: PPTX_DIGEST, slideCount: 30, renderSet: { images: powerPointImages } },
      libreOffice: { available: true, version: "26.2", isolatedProfile: true,
        invocationExitCode: 0, sourceUnchanged: true, packageReadbackReadable: true,
        slideCountMatchesInput: true, inputSha256: PPTX_DIGEST, inputSha256After: PPTX_DIGEST,
        slideCount: 30, renderSet: { images: libreOfficeImages } },
    },
    visualComparison: { comparisonStatus: "passed", failedSlideCount: 0,
      policy: { digest: `sha256:${"e".repeat(64)}`, thresholds: {
        changedPixelRatioMaxPpm: 3_000, block8LumaSsimMinMillionths: 980_000,
        meanAbsoluteChannelDeltaMaxMilli: 500, rmseMaxMilli: 500 } }, comparisons },
    rangeValidation: { available: true, evidenceClass: "actual_product_path", engine: "chromium",
      engineVersion: "140.0", deckId: "range-30", operationId: "operation-30",
      presentationId: "presentation-30", artifactSha256: PPTX_DIGEST, slideCount: 30, slides,
      totalVisibleTextRunCount: 150, totalMeasuredTextRunCount: 150, totalRangeRectCount: 180,
      nonFiniteRectCount: 0, zeroAreaRectCount: 0, unmeasuredVisibleTextRunCount: 0,
      outOfBoundsRectCount: 0, clippedTextRunCount: 0 },
    deploymentAuthorized: false,
  };
  report.bundleDigest = computeRenderRangeBundleDigest(report);
  return report;
}

function reseal(report) {
  report.bundleDigest = computeRenderRangeBundleDigest(report);
  return report;
}

test("accepts one immutable same-RC 30-slide product-path renderer and Range bundle", () => {
  const result = validateRenderRangeEvidence(passingReport());
  assert.equal(result.pass, true);
  assert.equal(result.releaseReady, true);
  assert.equal(result.deploymentAuthorized, false);
  assert.equal(result.bundleDigest, passingReport().bundleDigest);
  assert.deepEqual(result.immutableEvidenceBundleBinding, { available: false, valid: false });
  assert.equal(result.actualRendererEvidence.powerPoint.actualProductPath, true);
  assert.equal(result.actualRendererEvidence.libreOffice.actualProductPath, true);
  assert.equal(result.actualRendererEvidence.visualDiff.actualProductPath, true);
  assert.equal(result.actualRendererEvidence.domRange.actualProductPath, true);
  assert.equal(result.checks.every((entry) => entry.pass), true);
});

test("renderer unavailability is not release-ready", () => {
  const report = passingReport();
  report.renderers.libreOffice.available = false;
  const result = validateRenderRangeEvidence(reseal(report));
  assert.equal(result.pass, false);
  assert.equal(result.releaseReady, false);
  assert.equal(result.checks.find((entry) => entry.id === "libreoffice.available").pass, false);
});

test("fixtures and stale immutable bundle digests fail closed", () => {
  const fixture = passingReport();
  fixture.evidenceClass = "fixture";
  let result = validateRenderRangeEvidence(reseal(fixture));
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "contract.productPath").pass, false);

  const tampered = passingReport();
  tampered.deck.operationId = "operation-tampered-after-seal";
  result = validateRenderRangeEvidence(tampered);
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "contract.immutableBundle").pass, false);

  const deploymentClaim = passingReport();
  deploymentClaim.deploymentAuthorized = true;
  result = validateRenderRangeEvidence(reseal(deploymentClaim));
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "contract.deploymentSeparate").pass, false);

  const centralBinding = passingReport();
  centralBinding.immutableEvidenceBundleDigest = `sha256:${"f".repeat(64)}`;
  centralBinding.provenance.immutableEvidenceBundleDigest = centralBinding.immutableEvidenceBundleDigest;
  result = validateRenderRangeEvidence(reseal(centralBinding));
  assert.equal(result.pass, true);
  assert.deepEqual(result.immutableEvidenceBundleBinding, { available: true, valid: true });

  const unboundCentralPointer = passingReport();
  unboundCentralPointer.immutableEvidenceBundleDigest = `sha256:${"f".repeat(64)}`;
  result = validateRenderRangeEvidence(reseal(unboundCentralPointer));
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "contract.immutableEvidenceBundleBinding").pass, false);
});

test("visual metrics are derived against policy rather than trusting thresholdPassed", () => {
  const report = passingReport();
  report.visualComparison.comparisons[4].metrics.changedPixelRatioPpm = 3_001;
  report.visualComparison.comparisons[4].thresholdPassed = true;
  const result = validateRenderRangeEvidence(reseal(report));
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "visual.crossRenderer").pass, false);
});

test("Range coverage requires all 30 slides and rejects invalid geometry", () => {
  const missing = passingReport();
  missing.rangeValidation.slides.pop();
  let result = validateRenderRangeEvidence(reseal(missing));
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "range.coverage").pass, false);

  const invalid = passingReport();
  invalid.rangeValidation.slides[0].nonFiniteRectCount = 1;
  invalid.rangeValidation.nonFiniteRectCount = 1;
  result = validateRenderRangeEvidence(reseal(invalid));
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "range.coverage").pass, false);

  const dishonestAggregate = passingReport();
  dishonestAggregate.rangeValidation.totalMeasuredTextRunCount = 149;
  result = validateRenderRangeEvidence(reseal(dishonestAggregate));
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((entry) => entry.id === "range.coverage").pass, false);
});
