import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const RENDER_RANGE_EVIDENCE_SCHEMA = "public-service-render-range-evidence/v1";
export const RENDER_RANGE_EVALUATION_SCHEMA = "public-service-render-range-evaluation/v1";
export const RENDER_RANGE_RUNNER_PATH = "scripts/public-service-render-range-evidence.mjs";
export const REQUIRED_RANGE_SLIDE_COUNT = 30;

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation";

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function computeRenderRangeBundleDigest(report) {
  if (!report || typeof report !== "object" || Array.isArray(report)) return null;
  const unsigned = { ...report };
  delete unsigned.bundleDigest;
  return `sha256:${createHash("sha256").update(canonicalJson(unsigned)).digest("hex")}`;
}

function finiteInteger(value, minimum = 0) {
  return Number.isInteger(value) && value >= minimum;
}

function slideMap(entries) {
  const map = new Map();
  if (!Array.isArray(entries)) return map;
  for (const entry of entries) {
    if (finiteInteger(entry?.slideNumber, 1) && !map.has(entry.slideNumber)) map.set(entry.slideNumber, entry);
  }
  return map;
}

function exactSlideAxis(entries, expectedCount) {
  if (!Array.isArray(entries) || entries.length !== expectedCount) return false;
  const values = entries.map((entry) => entry?.slideNumber).sort((left, right) => left - right);
  return values.every((value, index) => value === index + 1);
}

function renderImageValid(image, expectedRenderer) {
  return image?.renderer === expectedRenderer
    && finiteInteger(image?.slideNumber, 1)
    && SHA256.test(String(image?.sha256 || ""))
    && finiteInteger(image?.bytes, 1)
    && finiteInteger(image?.widthPx, 1)
    && finiteInteger(image?.heightPx, 1);
}

function checkCollector(checks, id, pass, expected, actual) {
  checks.push({ id, pass: pass === true, expected, actual: actual ?? null });
}

/**
 * Fail-closed validation of evidence captured by the same immutable product RC.
 * This function validates a report; it never starts or attaches to PowerPoint,
 * LibreOffice, Chromium, a worker, or a provider.
 */
export function validateRenderRangeEvidence(report) {
  const checks = [];
  const deck = report?.deck || {};
  const artifact = deck?.artifact || {};
  const powerpoint = report?.renderers?.powerpoint || {};
  const libreoffice = report?.renderers?.libreOffice || {};
  const visual = report?.visualComparison || {};
  const range = report?.rangeValidation || {};
  const requiredSlides = REQUIRED_RANGE_SLIDE_COUNT;
  const immutablePackPointerSupplied = report?.immutableEvidenceBundleDigest !== undefined
    && report?.immutableEvidenceBundleDigest !== null;

  checkCollector(checks, "contract.schema", report?.schemaVersion === RENDER_RANGE_EVIDENCE_SCHEMA,
    RENDER_RANGE_EVIDENCE_SCHEMA, report?.schemaVersion);
  checkCollector(checks, "contract.productPath", report?.evidenceClass === "actual_product_path",
    "actual_product_path", report?.evidenceClass);
  checkCollector(checks, "contract.releaseCandidate", SHA256.test(String(report?.releaseCandidate || "")),
    "sha256:<64 lowercase hex>", report?.releaseCandidate);
  checkCollector(checks, "contract.sourceCommit", COMMIT.test(String(report?.sourceTreeCommit || "")),
    "40 lowercase hex commit", report?.sourceTreeCommit);
  checkCollector(checks, "contract.runner", report?.provenance?.runnerPath === RENDER_RANGE_RUNNER_PATH
    && SHA256.test(String(report?.provenance?.runnerSha256 || ""))
    && report?.provenance?.sourceManifestDigest === report?.releaseCandidate,
  "runner path/digest and source manifest bound to releaseCandidate", report?.provenance);
  checkCollector(checks, "contract.immutableBundle", SHA256.test(String(report?.bundleDigest || ""))
    && report?.bundleDigest === computeRenderRangeBundleDigest(report),
  "canonical sha256 over report excluding bundleDigest", report?.bundleDigest);
  checkCollector(checks, "contract.immutableEvidenceBundleBinding", !immutablePackPointerSupplied
    || (SHA256.test(String(report?.immutableEvidenceBundleDigest || ""))
      && report?.provenance?.immutableEvidenceBundleDigest === report?.immutableEvidenceBundleDigest),
  "optional sha256 pointer repeated in provenance when supplied",
  report?.immutableEvidenceBundleDigest ?? "unavailable");
  checkCollector(checks, "contract.deploymentSeparate", report?.deploymentAuthorized === false,
    false, report?.deploymentAuthorized);

  const deckIdentityValid = typeof deck.deckId === "string" && deck.deckId.length > 0
    && typeof deck.operationId === "string" && deck.operationId.length > 0
    && typeof deck.presentationId === "string" && deck.presentationId.length > 0;
  checkCollector(checks, "deck.productLineage", deckIdentityValid,
    "non-empty deckId, operationId, presentationId", deckIdentityValid);
  checkCollector(checks, "deck.largeRangeScenario", deck.slideCount === requiredSlides
    && artifact.slideCount === requiredSlides && artifact.mediaType === MEDIA_TYPE,
  `${requiredSlides}-slide PPTX`, { deckSlides: deck.slideCount, artifactSlides: artifact.slideCount,
    mediaType: artifact.mediaType });
  checkCollector(checks, "deck.artifactDigest", SHA256.test(String(artifact.sha256 || ""))
    && finiteInteger(artifact.bytes, 1), "immutable PPTX sha256 and positive byte count",
  { sha256: artifact.sha256, bytes: artifact.bytes });

  const powerpointAvailable = powerpoint.available === true;
  checkCollector(checks, "powerpoint.available", powerpointAvailable, true, powerpoint.available);
  checkCollector(checks, "powerpoint.ownedReopenReadback", powerpointAvailable
    && typeof powerpoint.version === "string" && powerpoint.version.length > 0
    && powerpoint.ownedProcessBindingExact === true
    && powerpoint.reopened === true && powerpoint.readbackPassed === true
    && powerpoint.firstOpenSaveCopyReopenReadbackPassed === true
    && powerpoint.firstOpenReopenedRenderHashesMatch === true
    && powerpoint.repairWarningObserved === false
    && powerpoint.unexpectedProcessCount === 0 && powerpoint.finalProcessCount === 0,
  "owned first-open/save-copy/reopen/readback with exact reopen render and no survivor",
  { version: powerpoint.version, ownedProcessBindingExact: powerpoint.ownedProcessBindingExact,
    reopened: powerpoint.reopened, readbackPassed: powerpoint.readbackPassed,
    exactReopenRender: powerpoint.firstOpenReopenedRenderHashesMatch,
    repairWarningObserved: powerpoint.repairWarningObserved,
    unexpectedProcessCount: powerpoint.unexpectedProcessCount, finalProcessCount: powerpoint.finalProcessCount });
  checkCollector(checks, "powerpoint.artifactBinding", powerpoint.inputSha256 === artifact.sha256
    && powerpoint.inputSha256After === artifact.sha256 && powerpoint.slideCount === requiredSlides,
  "same PPTX digest before/after and 30-slide readback",
  { before: powerpoint.inputSha256, after: powerpoint.inputSha256After, slides: powerpoint.slideCount });

  const powerPointImages = powerpoint?.renderSet?.images;
  const powerPointImageMap = slideMap(powerPointImages);
  checkCollector(checks, "powerpoint.renderSet", exactSlideAxis(powerPointImages, requiredSlides)
    && powerPointImageMap.size === requiredSlides
    && powerPointImages.every((image) => renderImageValid(image, "powerpoint")),
  "one digest-bound PowerPoint render per slide", Array.isArray(powerPointImages) ? powerPointImages.length : null);

  const libreOfficeAvailable = libreoffice.available === true;
  checkCollector(checks, "libreoffice.available", libreOfficeAvailable, true, libreoffice.available);
  checkCollector(checks, "libreoffice.isolatedReadback", libreOfficeAvailable
    && typeof libreoffice.version === "string" && libreoffice.version.length > 0
    && libreoffice.isolatedProfile === true && libreoffice.invocationExitCode === 0
    && libreoffice.sourceUnchanged === true && libreoffice.packageReadbackReadable === true
    && libreoffice.slideCountMatchesInput === true,
  "isolated LibreOffice conversion/readback with unchanged source",
  { version: libreoffice.version, isolatedProfile: libreoffice.isolatedProfile,
    invocationExitCode: libreoffice.invocationExitCode, sourceUnchanged: libreoffice.sourceUnchanged,
    packageReadbackReadable: libreoffice.packageReadbackReadable,
    slideCountMatchesInput: libreoffice.slideCountMatchesInput });
  checkCollector(checks, "libreoffice.artifactBinding", libreoffice.inputSha256 === artifact.sha256
    && libreoffice.inputSha256After === artifact.sha256 && libreoffice.slideCount === requiredSlides,
  "same PPTX digest before/after and 30-slide readback",
  { before: libreoffice.inputSha256, after: libreoffice.inputSha256After, slides: libreoffice.slideCount });

  const libreOfficeImages = libreoffice?.renderSet?.images;
  const libreOfficeImageMap = slideMap(libreOfficeImages);
  checkCollector(checks, "libreoffice.renderSet", exactSlideAxis(libreOfficeImages, requiredSlides)
    && libreOfficeImageMap.size === requiredSlides
    && libreOfficeImages.every((image) => renderImageValid(image, "libreoffice")),
  "one digest-bound LibreOffice render per slide", Array.isArray(libreOfficeImages) ? libreOfficeImages.length : null);

  const thresholds = visual?.policy?.thresholds || {};
  const thresholdContract = SHA256.test(String(visual?.policy?.digest || ""))
    && finiteInteger(thresholds.changedPixelRatioMaxPpm)
    && finiteInteger(thresholds.block8LumaSsimMinMillionths)
    && thresholds.block8LumaSsimMinMillionths <= 1_000_000
    && finiteInteger(thresholds.meanAbsoluteChannelDeltaMaxMilli)
    && finiteInteger(thresholds.rmseMaxMilli);
  checkCollector(checks, "visual.policy", thresholdContract,
    "digest-bound finite visual thresholds", visual?.policy);

  const comparisons = visual?.comparisons;
  let comparisonContentValid = exactSlideAxis(comparisons, requiredSlides);
  if (comparisonContentValid) {
    comparisonContentValid = comparisons.every((comparison) => {
      const pp = powerPointImageMap.get(comparison.slideNumber);
      const lo = libreOfficeImageMap.get(comparison.slideNumber);
      const metrics = comparison.metrics || {};
      const metricsFinite = finiteInteger(metrics.changedPixelRatioPpm)
        && finiteInteger(metrics.block8LumaSsimMillionths)
        && metrics.block8LumaSsimMillionths <= 1_000_000
        && finiteInteger(metrics.meanAbsoluteChannelDeltaMilli)
        && finiteInteger(metrics.rmseMilli);
      const derivedPass = thresholdContract && metricsFinite
        && metrics.changedPixelRatioPpm <= thresholds.changedPixelRatioMaxPpm
        && metrics.block8LumaSsimMillionths >= thresholds.block8LumaSsimMinMillionths
        && metrics.meanAbsoluteChannelDeltaMilli <= thresholds.meanAbsoluteChannelDeltaMaxMilli
        && metrics.rmseMilli <= thresholds.rmseMaxMilli;
      return pp && lo
        && comparison.powerPointImageSha256 === pp.sha256
        && comparison.libreOfficeImageSha256 === lo.sha256
        && comparison.widthPx === pp.widthPx && comparison.widthPx === lo.widthPx
        && comparison.heightPx === pp.heightPx && comparison.heightPx === lo.heightPx
        && comparison.thresholdPassed === derivedPass && derivedPass;
    });
  }
  checkCollector(checks, "visual.crossRenderer", comparisonContentValid
    && visual.comparisonStatus === "passed" && visual.failedSlideCount === 0,
  "30 digest-bound PowerPoint/LibreOffice comparisons within policy",
  { comparisonStatus: visual.comparisonStatus, failedSlideCount: visual.failedSlideCount,
    comparisons: Array.isArray(comparisons) ? comparisons.length : null });

  const rangeSamples = range?.slides;
  let rangeSamplesValid = exactSlideAxis(rangeSamples, requiredSlides);
  if (rangeSamplesValid) {
    rangeSamplesValid = rangeSamples.every((sample) => finiteInteger(sample.visibleTextRunCount, 1)
      && sample.measuredTextRunCount === sample.visibleTextRunCount
      && finiteInteger(sample.domRangeRectCount, sample.measuredTextRunCount)
      && sample.nonFiniteRectCount === 0 && sample.zeroAreaRectCount === 0
      && sample.unmeasuredVisibleTextRunCount === 0 && sample.outOfBoundsRectCount === 0
      && sample.clippedTextRunCount === 0);
  }
  checkCollector(checks, "range.productPath", range.available === true
    && range.evidenceClass === "actual_product_path" && range.engine === "chromium"
    && typeof range.engineVersion === "string" && range.engineVersion.length > 0
    && range.deckId === deck.deckId && range.operationId === deck.operationId
    && range.presentationId === deck.presentationId
    && range.artifactSha256 === artifact.sha256 && range.slideCount === requiredSlides,
  "Chromium product-path Range capture bound to the 30-slide PPTX",
  { available: range.available, evidenceClass: range.evidenceClass, engine: range.engine,
    engineVersion: range.engineVersion, deckId: range.deckId, operationId: range.operationId,
    presentationId: range.presentationId, artifactSha256: range.artifactSha256,
    slideCount: range.slideCount });
  const rangeTotals = Array.isArray(rangeSamples) ? rangeSamples.reduce((totals, sample) => ({
    visible: totals.visible + (Number.isInteger(sample?.visibleTextRunCount) ? sample.visibleTextRunCount : 0),
    measured: totals.measured + (Number.isInteger(sample?.measuredTextRunCount) ? sample.measuredTextRunCount : 0),
    rects: totals.rects + (Number.isInteger(sample?.domRangeRectCount) ? sample.domRangeRectCount : 0),
    nonFinite: totals.nonFinite + (Number.isInteger(sample?.nonFiniteRectCount) ? sample.nonFiniteRectCount : 0),
    zeroArea: totals.zeroArea + (Number.isInteger(sample?.zeroAreaRectCount) ? sample.zeroAreaRectCount : 0),
    unmeasured: totals.unmeasured + (Number.isInteger(sample?.unmeasuredVisibleTextRunCount) ? sample.unmeasuredVisibleTextRunCount : 0),
    outOfBounds: totals.outOfBounds + (Number.isInteger(sample?.outOfBoundsRectCount) ? sample.outOfBoundsRectCount : 0),
    clipped: totals.clipped + (Number.isInteger(sample?.clippedTextRunCount) ? sample.clippedTextRunCount : 0),
  }), { visible: 0, measured: 0, rects: 0, nonFinite: 0, zeroArea: 0,
    unmeasured: 0, outOfBounds: 0, clipped: 0 }) : null;
  checkCollector(checks, "range.coverage", rangeSamplesValid && rangeTotals !== null
    && range.totalVisibleTextRunCount === rangeTotals.visible
    && range.totalMeasuredTextRunCount === rangeTotals.measured
    && range.totalRangeRectCount === rangeTotals.rects
    && range.nonFiniteRectCount === rangeTotals.nonFinite
    && range.zeroAreaRectCount === rangeTotals.zeroArea
    && range.unmeasuredVisibleTextRunCount === rangeTotals.unmeasured
    && range.outOfBoundsRectCount === rangeTotals.outOfBounds
    && range.clippedTextRunCount === rangeTotals.clipped
    && rangeTotals.visible === rangeTotals.measured && rangeTotals.rects >= rangeTotals.measured
    && rangeTotals.nonFinite === 0 && rangeTotals.zeroArea === 0
    && rangeTotals.unmeasured === 0 && rangeTotals.outOfBounds === 0 && rangeTotals.clipped === 0,
  "complete non-empty DOM Range coverage without invalid/clipped/out-of-bounds text",
  { slides: Array.isArray(rangeSamples) ? rangeSamples.length : null,
    visible: range.totalVisibleTextRunCount, measured: range.totalMeasuredTextRunCount,
    rects: range.totalRangeRectCount, nonFinite: range.nonFiniteRectCount,
    zeroArea: range.zeroAreaRectCount, unmeasured: range.unmeasuredVisibleTextRunCount,
    outOfBounds: range.outOfBoundsRectCount, clipped: range.clippedTextRunCount });

  const pass = checks.every((check) => check.pass);
  const checkPassed = (id) => checks.find((check) => check.id === id)?.pass === true;
  return {
    schemaVersion: RENDER_RANGE_EVALUATION_SCHEMA,
    bundleDigest: report?.bundleDigest || null,
    immutableEvidenceBundleDigest: report?.immutableEvidenceBundleDigest || null,
    immutableEvidenceBundleBinding: {
      available: immutablePackPointerSupplied,
      valid: immutablePackPointerSupplied
        && checks.find((check) => check.id === "contract.immutableEvidenceBundleBinding")?.pass === true,
    },
    releaseCandidate: report?.releaseCandidate || null,
    sourceTreeCommit: report?.sourceTreeCommit || null,
    evidenceClass: report?.evidenceClass || null,
    pass,
    releaseReady: pass,
    deploymentAuthorized: false,
    actualRendererEvidence: {
      powerPoint: {
        actualProductPath: checkPassed("contract.productPath")
          && checkPassed("powerpoint.available") && checkPassed("powerpoint.ownedReopenReadback")
          && checkPassed("powerpoint.artifactBinding") && checkPassed("powerpoint.renderSet"),
        version: powerpoint.version || null,
        slideCount: powerpoint.slideCount ?? null,
        artifactSha256: powerpoint.inputSha256 || null,
      },
      libreOffice: {
        actualProductPath: checkPassed("contract.productPath")
          && checkPassed("libreoffice.available") && checkPassed("libreoffice.isolatedReadback")
          && checkPassed("libreoffice.artifactBinding") && checkPassed("libreoffice.renderSet"),
        version: libreoffice.version || null,
        slideCount: libreoffice.slideCount ?? null,
        artifactSha256: libreoffice.inputSha256 || null,
      },
      visualDiff: {
        actualProductPath: checkPassed("contract.productPath") && checkPassed("visual.crossRenderer"),
        comparisonCount: Array.isArray(comparisons) ? comparisons.length : 0,
        failedSlideCount: visual.failedSlideCount ?? null,
        policyDigest: visual?.policy?.digest || null,
      },
      domRange: {
        actualProductPath: checkPassed("range.productPath") && checkPassed("range.coverage"),
        engineVersion: range.engineVersion || null,
        slideCount: range.slideCount ?? null,
        measuredTextRunCount: range.totalMeasuredTextRunCount ?? null,
      },
    },
    checks,
  };
}

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error(`invalid argument: ${key}`);
    args[key.slice(2)] = value;
    index += 1;
  }
  return args;
}

async function atomicJson(target, payload) {
  await mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
  await rename(temporary, target);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.input) throw new Error("required: --input <public-service-render-range-evidence/v1.json> [--output <evaluation.json>]");
  const report = JSON.parse(await readFile(path.resolve(args.input), "utf8"));
  const evaluation = validateRenderRangeEvidence(report);
  if (args.output) await atomicJson(path.resolve(args.output), evaluation);
  process.stdout.write(`${JSON.stringify(evaluation)}\n`);
  if (!evaluation.pass) process.exitCode = 1;
}

if (path.resolve(process.argv[1] || "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}
