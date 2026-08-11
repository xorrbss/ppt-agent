import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import sharp from "sharp";

import {
  DEFAULT_THRESHOLDS,
  KNOWN_TEXT_LAYOUT_OBSERVATIONS,
  VIEWPORT,
  aggregateLayoutMetrics,
  applyLayoutTextPolicy,
  classifyFailure,
  comparePngBuffers,
  sha256,
} from "./template-v2-pdf-golden-metrics.mjs";
import {
  converterPath,
  chromeProvenance as collectChromeProvenance,
  fontProvenance,
  renderPdfPages,
  resolveChromeExecutable,
  resolvePopplerExecutables,
  runPresentationExport,
  spawnCapture,
  startGoldenServer,
  startSelectionProxy,
  toolProvenance,
  validateGatePaths,
  writeJsonAtomic,
} from "./template-v2-pdf-golden-runtime.mjs";

async function sha256File(filePath) {
  return sha256(await readFile(filePath));
}

export const TEMPLATE_IDS = Object.freeze([
  "dynamic",
  "editorial",
  "executive",
  "general",
  "modern",
  "momentum",
  "standard",
  "swift",
]);

const SHARED_FULL_GATE_PATTERNS = Object.freeze([
  /^presentation-export\//,
  /^scripts\/(?:sync-presentation-export|lib\/presentation-export-readiness)/,
  /^servers\/nextjs\/app\/\(export\)\//,
  /^servers\/nextjs\/app\/api\/export-presentation\//,
  /^servers\/nextjs\/lib\/(?:template-v2-json-to-html|template-v2-general-renderer|template-v2-render-plan|presentation-export-|run-bundled-presentation-export)/,
  /^servers\/nextjs\/scripts\/(?:lib\/)?template-v2-pdf-golden/,
  /^\.github\/workflows\/template-v2-export-fidelity\.yml$/,
]);

function normalizedRepoPath(value) {
  return value.replaceAll("\\", "/").replace(/^\.\//, "");
}

export async function loadGoldenInventory(repositoryRoot) {
  const templates = [];
  for (const templateId of TEMPLATE_IDS) {
    const templatePath = path.join(
      repositoryRoot,
      "templates",
      templateId,
      "template.json",
    );
    const manifestPath = path.join(
      repositoryRoot,
      "templates",
      "manifests",
      `${templateId}.browser-golden.v1.json`,
    );
    const [templateBytes, manifestBytes] = await Promise.all([
      readFile(templatePath),
      readFile(manifestPath),
    ]);
    const template = JSON.parse(templateBytes);
    const manifest = JSON.parse(manifestBytes);
    assert.equal(template.id, templateId);
    assert.equal(manifest.templateId, templateId);
    assert.deepEqual(
      manifest.layoutIds,
      template.layouts.map((layout) => layout.id),
      `${templateId} browser golden layout order drifted`,
    );
    assert.equal(manifest.screenshots.length, template.layouts.length);
    assert.deepEqual(
      manifest.viewport,
      { width: VIEWPORT.width, height: VIEWPORT.height },
      `${templateId} viewport drifted`,
    );
    assert.equal(manifest.entrypointSha256, sha256(templateBytes));

    const layouts = [];
    for (const [index, screenshot] of manifest.screenshots.entries()) {
      assert.equal(screenshot.layoutId, template.layouts[index].id);
      assert.equal(screenshot.width, VIEWPORT.width);
      assert.equal(screenshot.height, VIEWPORT.height);
      const absolutePath = path.join(repositoryRoot, screenshot.path);
      const bytes = await readFile(absolutePath);
      assert.equal(
        sha256(bytes),
        screenshot.sha256,
        `${templateId}/${screenshot.layoutId} baseline hash drifted`,
      );
      layouts.push({
        index,
        layoutId: screenshot.layoutId,
        baselinePath: screenshot.path,
        baselineSha256: screenshot.sha256,
        absoluteBaselinePath: absolutePath,
      });
    }
    templates.push({
      templateId,
      templatePath,
      templateSha256: sha256(templateBytes),
      manifestPath,
      manifestSha256: sha256(manifestBytes),
      rendererSha256: manifest.renderer.moduleSha256,
      browserReceipt: manifest.browser,
      layouts,
    });
  }
  const totalLayouts = templates.reduce(
    (total, template) => total + template.layouts.length,
    0,
  );
  assert.equal(totalLayouts, 158, "PDF golden inventory must contain 158 layouts");
  assert.equal(
    new Set(templates.map((template) => template.rendererSha256)).size,
    1,
    "browser golden receipts must share one renderer digest",
  );
  return { templates, totalLayouts };
}

export function selectGoldenLayouts(inventory, { full = false, changedFiles = [] } = {}) {
  const normalized = changedFiles.map(normalizedRepoPath);
  const sharedChange = normalized.some((file) =>
    SHARED_FULL_GATE_PATTERNS.some((pattern) => pattern.test(file)),
  );
  const fullTemplates = new Set();
  for (const file of normalized) {
    const match = /^templates\/([^/]+)\//.exec(file);
    if (match && TEMPLATE_IDS.includes(match[1])) fullTemplates.add(match[1]);
  }
  const selectAll = full || sharedChange;
  const templates = inventory.templates.map((template) => ({
    ...template,
    selectedLayouts:
      selectAll || fullTemplates.has(template.templateId)
        ? [...template.layouts]
        : template.layouts.slice(0, 1),
  }));
  return {
    scope: selectAll ? "full" : "representative-plus-changed",
    sharedChange,
    changedFiles: normalized,
    templates,
    selectedLayoutCount: templates.reduce(
      (total, template) => total + template.selectedLayouts.length,
      0,
    ),
  };
}


async function writeFailureImages({ actualPath, expectedPath, comparison, textBoxes, failureRoot, stem }) {
  await mkdir(failureRoot, { recursive: true });
  const [actual, expected] = await Promise.all([readFile(actualPath), readFile(expectedPath)]);
  await copyFile(actualPath, path.join(failureRoot, `${stem}.actual.png`));
  const actualRaw = await sharp(actual).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  const expectedRaw = await sharp(expected).removeAlpha().raw().toBuffer({ resolveWithObject: true });
  const diff = Buffer.alloc(actualRaw.data.length);
  for (let index = 0; index < comparison.badMask.length; index += 1) {
    const offset = index * 3;
    if (comparison.badMask[index] !== 0) {
      diff[offset] = 255;
      diff[offset + 1] = Math.floor(expectedRaw.data[offset + 1] * 0.2);
      diff[offset + 2] = Math.floor(expectedRaw.data[offset + 2] * 0.2);
    } else {
      const gray = Math.floor(
        (actualRaw.data[offset] + actualRaw.data[offset + 1] + actualRaw.data[offset + 2]) / 6,
      );
      diff[offset] = gray;
      diff[offset + 1] = gray;
      diff[offset + 2] = gray;
    }
  }
  await sharp(diff, { raw: actualRaw.info }).png().toFile(path.join(failureRoot, `${stem}.diff.png`));
  const rectangles = (textBoxes ?? [])
    .map((box) => `<rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}"/>`)
    .join("");
  const overlay = Buffer.from(
    `<svg width="${VIEWPORT.width}" height="${VIEWPORT.height}" xmlns="http://www.w3.org/2000/svg"><g fill="none" stroke="#ff00ff" stroke-width="1">${rectangles}</g></svg>`,
  );
  await sharp(actual).composite([{ input: overlay }]).png().toFile(path.join(failureRoot, `${stem}.bbox.png`));
}

async function preserveOperationalFailure({ templateId, templateWork, artifactRoot, error, pdfPath }) {
  const destination = path.join(artifactRoot, templateId, "operational");
  await mkdir(destination, { recursive: true });
  await writeFile(
    path.join(destination, "error.txt"),
    `${error instanceof Error ? error.stack : String(error)}\n`,
  );
  if (pdfPath && existsSync(pdfPath)) {
    await copyFile(pdfPath, path.join(destination, `${templateId}.pdf`));
  }
  const pagesRoot = path.join(templateWork, "pages");
  if (existsSync(pagesRoot)) {
    const pageNames = (await readdir(pagesRoot)).filter((name) => /^page-\d+\.png$/i.test(name));
    if (pageNames.length) {
      const pageDestination = path.join(destination, "rendered-pages");
      await mkdir(pageDestination, { recursive: true });
      for (const name of pageNames) {
        await copyFile(path.join(pagesRoot, name), path.join(pageDestination, name));
      }
    }
  }
}


export async function runPdfGoldenGate({
  repositoryRoot,
  full = false,
  changedFiles = [],
  receiptPath = path.join(repositoryRoot, "templates", "manifests", "template-v2.pdf-golden.v1.json"),
  artifactRoot = process.env.TEST_ARTIFACT_DIR || path.join(repositoryRoot, "test-artifacts", "template-v2-pdf-golden"),
  thresholds = DEFAULT_THRESHOLDS,
} = {}) {
  assert.ok(repositoryRoot, "repositoryRoot is required");
  const safePaths = await validateGatePaths({ repositoryRoot, artifactRoot, receiptPath });
  repositoryRoot = safePaths.repository;
  artifactRoot = safePaths.artifactRoot;
  receiptPath = safePaths.receiptPath;
  const inventory = await loadGoldenInventory(repositoryRoot);
  const selection = selectGoldenLayouts(inventory, { full, changedFiles });
  const chrome = resolveChromeExecutable();
  assert.ok(chrome, "Chrome/Chromium is required");
  const poppler = resolvePopplerExecutables();
  const runtimeVersion = (await readFile(path.join(repositoryRoot, "presentation-export", ".installed-version"), "utf8")).trim();
  assert.equal(runtimeVersion, "v0.4.3", "PDF golden gate requires presentation-export v0.4.3");
  const workRoot = await mkdtemp(path.join(os.tmpdir(), "presenton-pdf-golden-"));
  const failures = [];
  const templateResults = [];
  try {
    for (const template of selection.templates) {
      const templateWork = path.join(workRoot, template.templateId);
      await mkdir(templateWork, { recursive: true });
      const goldenServer = await startGoldenServer(repositoryRoot, template.templateId);
      const proxy = await startSelectionProxy(template.selectedLayouts);
      let pdfPath;
      try {
        pdfPath = await runPresentationExport({
          repositoryRoot,
          url: proxy.url,
          title: `template-v2-${template.templateId}-pdf-golden`,
          workRoot: templateWork,
          chrome,
        });
        const pagePaths = await renderPdfPages({
          pdftoppm: poppler.pdftoppm,
          pdfPath,
          outputRoot: path.join(templateWork, "pages"),
          expectedPages: template.selectedLayouts.length,
        });
        const telemetry = proxy.getTelemetry();
        assert.equal(telemetry?.layouts?.length, template.selectedLayouts.length, `${template.templateId} telemetry missing`);
        const layoutResults = [];
        for (const [pageIndex, layout] of template.selectedLayouts.entries()) {
          const actualPath = pagePaths[pageIndex];
          const actualMetadata = await sharp(actualPath).metadata();
          assert.equal(actualMetadata.width, VIEWPORT.width);
          assert.equal(actualMetadata.height, VIEWPORT.height);
          const layoutTelemetry = telemetry.layouts[pageIndex];
          const comparison = await comparePngBuffers(
            await readFile(actualPath),
            await readFile(layout.absoluteBaselinePath),
            { textBoxes: layoutTelemetry.textBoxes, thresholds },
          );
          const textPolicy = applyLayoutTextPolicy({
            templateId: template.templateId,
            layoutId: layout.layoutId,
            textBbox: comparison.textBbox,
            telemetry: layoutTelemetry,
            failures: comparison.failures,
            thresholds,
          });
          comparison.failures = textPolicy.failures;
          comparison.passed = textPolicy.passed;
          const result = {
            layoutId: layout.layoutId,
            baselineSha256: layout.baselineSha256,
            metrics: comparison.metrics,
            textBbox: comparison.textBbox,
            telemetry: {
              fontSetStatus: layoutTelemetry.fontSetStatus,
              failedFonts: layoutTelemetry.failedFonts,
              overflowElements: layoutTelemetry.overflowElements,
              clippedText: layoutTelemetry.clippedText,
              transparencyCount: layoutTelemetry.transparencyCount,
              svgCount: layoutTelemetry.svgCount,
              chartCanvasCount: layoutTelemetry.chartCanvasCount,
            },
            passed: comparison.passed && layoutTelemetry.fontSetStatus === "loaded" && layoutTelemetry.failedFonts === 0,
            ...(textPolicy.acceptedObservation
              ? { acceptedKnownObservation: textPolicy.acceptedObservation }
              : {}),
          };
          if (!result.passed) {
            result.reason = classifyFailure({ telemetry: result.telemetry, comparison });
            result.failures = comparison.failures;
            failures.push({ templateId: template.templateId, layoutId: layout.layoutId, reason: result.reason });
            const failureRoot = path.join(artifactRoot, template.templateId);
            await writeFailureImages({
              actualPath,
              expectedPath: layout.absoluteBaselinePath,
              comparison,
              textBoxes: layoutTelemetry.textBoxes,
              failureRoot,
              stem: `${String(pageIndex + 1).padStart(2, "0")}-${layout.layoutId}`,
            });
          }
          layoutResults.push(result);
        }
        if (layoutResults.some((layout) => !layout.passed)) {
          await mkdir(path.join(artifactRoot, template.templateId), { recursive: true });
          await copyFile(pdfPath, path.join(artifactRoot, template.templateId, `${template.templateId}.pdf`));
        }
        templateResults.push({
          templateId: template.templateId,
          selectedPageCount: template.selectedLayouts.length,
          pdfSha256: await sha256File(pdfPath),
          metrics: aggregateLayoutMetrics(layoutResults),
          layouts: layoutResults,
        });
      } catch (error) {
        await preserveOperationalFailure({
          templateId: template.templateId,
          templateWork,
          artifactRoot,
          error,
          pdfPath,
        });
        throw error;
      } finally {
        await proxy.close();
        await goldenServer.close();
      }
      process.stdout.write(`${template.templateId}: ${template.selectedLayouts.length} PDF pages verified\n`);
    }

    const [chromeProvenance, pdftoppmProvenance, pdfinfoProvenance, converterProvenance] = await Promise.all([
      collectChromeProvenance(chrome, repositoryRoot),
      toolProvenance(poppler.pdftoppm, ["-v"], repositoryRoot),
      toolProvenance(poppler.pdfinfo, ["-v"], repositoryRoot),
      toolProvenance(converterPath(repositoryRoot), ["--version"], repositoryRoot).catch(async () => ({
        path: normalizedRepoPath(path.relative(repositoryRoot, converterPath(repositoryRoot))),
        version: "PyInstaller converter bundled with presentation-export v0.4.3",
        sha256: await sha256File(converterPath(repositoryRoot)),
        sizeBytes: (await stat(converterPath(repositoryRoot))).size,
      })),
    ]);
    const fonts = [];
    for (const templateId of TEMPLATE_IDS) fonts.push(await fontProvenance(repositoryRoot, templateId));
    const allLayouts = templateResults.flatMap((template) => template.layouts);
    const observedMetrics = aggregateLayoutMetrics(allLayouts);
    const acceptedKnownObservations = templateResults.flatMap((template) =>
      template.layouts
        .filter((layout) => layout.acceptedKnownObservation)
        .map((layout) => ({
          templateId: template.templateId,
          layoutId: layout.layoutId,
          ...layout.acceptedKnownObservation,
        })),
    );
    const receipt = {
      schemaVersion: "presenton.template-v2-pdf-golden/v1",
      status: failures.length === 0 ? "passed" : "failed",
      generatedAt: new Date().toISOString(),
      scope: selection.scope,
      viewport: VIEWPORT,
      thresholds,
      thresholdPolicy: {
        calibration: {
          basis: "Existing 158 browser PNG baselines rendered through presentation-export v0.4.3 and Poppler at 96 DPI; thresholds bound observed Chrome print/Poppler antialiasing without accepting page compositing drift.",
          initialCandidate: {
            meanAbsoluteError: 0.03,
            perceptualBadPixelRatio: 0.05,
            ssim: 0.92,
            largestBadComponent: 25_000,
            textBboxMeanAbsoluteError: 0.2,
            textBboxOutOfBounds: 0,
          },
          effective: thresholds,
          changes: [
            "perceptualBadPixelRatio 0.05 -> 0.07 for Poppler/browser edge antialiasing",
            "ssim 0.92 -> 0.90 for text rasterization variance",
            "textBboxMeanAbsoluteError 0.20 -> 0.25 for localized glyph rasterization variance",
          ],
          observedMaxima: observedMetrics,
        },
        textLayout: {
          default: "Fail closed at 0 out-of-bounds text ranges, 0 clipped direct-text elements, and 0 overflowing direct-text elements.",
          knownObservationDefinitions: Object.entries(KNOWN_TEXT_LAYOUT_OBSERVATIONS).map(
            ([layout, observation]) => ({ layout, ...observation }),
          ),
          acceptedKnownObservations,
        },
      },
      baseline: {
        kind: "existing-browser-png-only",
        duplicatePdfOrPngBaselines: false,
        totalLayouts: inventory.totalLayouts,
        rendererSha256: inventory.templates[0].rendererSha256,
        templates: inventory.templates.map((template) => ({
          templateId: template.templateId,
          templateSha256: template.templateSha256,
          browserGoldenManifestSha256: template.manifestSha256,
          layoutCount: template.layouts.length,
        })),
      },
      provenance: {
        presentationExport: {
          version: runtimeVersion,
          entrypointSha256: await sha256File(path.join(repositoryRoot, "presentation-export", "index.cjs")),
          converter: converterProvenance,
        },
        chrome: chromeProvenance,
        poppler: { pdftoppm: pdftoppmProvenance, pdfinfo: pdfinfoProvenance },
        fonts,
      },
      summary: {
        templateCount: templateResults.length,
        pageCount: allLayouts.length,
        passedPages: allLayouts.filter((layout) => layout.passed).length,
        failedPages: failures.length,
        metrics: observedMetrics,
        failureReasons: Object.fromEntries(
          [...new Set(failures.map((failure) => failure.reason))].sort().map((reason) => [
            reason,
            failures.filter((failure) => failure.reason === reason).length,
          ]),
        ),
      },
      templates: templateResults,
    };
    if (failures.length) {
      await mkdir(artifactRoot, { recursive: true });
      await writeFile(path.join(artifactRoot, "receipt.json"), `${JSON.stringify(receipt, null, 2)}\n`);
      throw new Error(`PDF golden gate failed for ${failures.length} layouts: ${JSON.stringify(failures.slice(0, 20))}`);
    }
    await writeJsonAtomic(receiptPath, receipt);
    await rm(artifactRoot, { recursive: true, force: true });
    return receipt;
  } finally {
    await rm(workRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}


export async function changedFilesFromGit(repositoryRoot, baseRef) {
  if (!baseRef) return [];
  const result = await spawnCapture("git", ["diff", "--name-only", `${baseRef}...HEAD`], { cwd: repositoryRoot });
  return result.stdout.split(/\r?\n/).filter(Boolean);
}
