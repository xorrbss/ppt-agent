import fs from "node:fs/promises";
import path from "node:path";

import sharp from "sharp";

import {
  compareSlidePngs,
  DEFAULT_FIDELITY_TOLERANCES,
} from "../lib/export-fidelity/image-compare.mjs";
import {
  renderPptxToPngPages,
  resolvePptxRenderTools,
} from "../lib/export-fidelity/pptx-render.mjs";
import {
  analyzeDiffRegions,
  compareAgainstBaseline,
  summarizeFallbackQuality,
} from "./lib/qa-visual-diagnostics.mjs";

function usage() {
  throw new Error(
    "Usage: qa-authored-hybrid-visual.mjs <source.pptx> <editable.pptx> " +
      "<authored-png-directory> <qa-directory> " +
      "[--baseline-metrics <visual-metrics.json>] [--quality-json <quality.json>] " +
      "[--element-map <element-map.json>] [--focus-slides 5,8,12,18] " +
      "[--max-regions 12] [--fail-on-regression] [--overwrite]"
  );
}

function parseArguments(argv) {
  const positional = [];
  const options = {
    focusSlides: [5, 8, 12, 18, 4, 6, 7, 16, 19, 20],
    maxRegions: 12,
    failOnRegression: false,
    overwrite: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) {
      positional.push(value);
      continue;
    }
    if (value === "--fail-on-regression") {
      options.failOnRegression = true;
      continue;
    }
    if (value === "--overwrite") {
      options.overwrite = true;
      continue;
    }
    const next = argv[++index];
    if (!next) usage();
    if (value === "--baseline-metrics") options.baselineMetrics = next;
    else if (value === "--quality-json") options.qualityJson = next;
    else if (value === "--element-map") options.elementMap = next;
    else if (value === "--focus-slides") {
      options.focusSlides = next
        .split(",")
        .map((item) => Number.parseInt(item.trim(), 10))
        .filter(Number.isFinite);
    } else if (value === "--max-regions") {
      options.maxRegions = Number.parseInt(next, 10);
      if (!Number.isInteger(options.maxRegions) || options.maxRegions < 1) usage();
    } else {
      usage();
    }
  }
  if (positional.length !== 4) usage();
  return {
    sourcePptx: positional[0],
    editablePptx: positional[1],
    authoredDirectory: positional[2],
    qaDirectory: positional[3],
    ...options,
  };
}

async function readOptionalJson(filePath) {
  if (!filePath) return null;
  try {
    const parsed = JSON.parse(await fs.readFile(filePath, "utf8"));
    parsed.__path = path.resolve(filePath);
    return parsed;
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function discoverAuthoredPages(directory) {
  const entries = await fs.readdir(directory);
  const pages = entries
    .map((name) => {
      const match = /^slide_(\d+)\.png$/i.exec(name);
      return match
        ? { index: Number.parseInt(match[1], 10), filePath: path.join(directory, name) }
        : null;
    })
    .filter(Boolean)
    .sort((left, right) => left.index - right.index);
  if (!pages.length) {
    throw new Error(`No slide_<index>.png files found in ${directory}.`);
  }
  for (const [expected, page] of pages.entries()) {
    if (page.index !== expected) {
      throw new Error(
        `Authored PNG sequence is not contiguous: expected slide_${expected}.png.`
      );
    }
  }
  return Promise.all(
    pages.map(async (page) => {
      const png = await fs.readFile(page.filePath);
      const metadata = await sharp(png).metadata();
      if (metadata.width !== 1280 || metadata.height !== 720) {
        throw new Error(
          `Authored slide ${page.index + 1} is ${metadata.width}x${metadata.height}, ` +
            "expected 1280x720."
        );
      }
      return png;
    })
  );
}

function summarize(rows, key) {
  const values = rows.map((row) => row[key]);
  return {
    passedSlides: values.filter((value) => value.passed).length,
    meanAbsoluteErrorAverage:
      values.reduce((sum, value) => sum + value.meanAbsoluteError, 0) /
      values.length,
    badPixelRatioAverage:
      values.reduce((sum, value) => sum + value.badPixelRatio, 0) /
      values.length,
    largestBadComponentMaximum: Math.max(
      ...values.map((value) => value.largestBadComponentPixels)
    ),
  };
}

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function regionOverlay(regions) {
  const markup = regions
    .map(
      (region, index) =>
        `<rect x="${region.x}" y="${region.y}" width="${region.width}" ` +
        `height="${region.height}" fill="none" stroke="#00e5ff" stroke-width="2"/>` +
        `<rect x="${region.x}" y="${Math.max(0, region.y - 18)}" width="160" ` +
        `height="18" fill="#102a56" fill-opacity="0.9"/>` +
        `<text x="${region.x + 4}" y="${Math.max(13, region.y - 5)}" fill="white" ` +
        `font-family="Arial" font-size="12">#${index + 1} ${escapeXml(
          region.heuristic
        )}</text>`
    )
    .join("");
  return Buffer.from(
    `<svg width="1280" height="720" xmlns="http://www.w3.org/2000/svg">${markup}</svg>`
  );
}

function slideElements(elementMap, slideNumber) {
  if (!elementMap) return [];
  const slide = (elementMap.slides ?? []).find(
    (candidate) => candidate.slideNumber === slideNumber
  );
  return slide?.elements ?? [];
}

const options = parseArguments(process.argv.slice(2));
const {
  sourcePptx,
  editablePptx,
  authoredDirectory,
  qaDirectory,
  maxRegions,
} = options;

const sentinel = path.join(qaDirectory, "visual-metrics.json");
if (!options.overwrite) {
  try {
    await fs.access(sentinel);
    throw new Error(
      `QA output already exists at ${sentinel}; use a new timestamped directory.`
    );
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
}

const authoredPages = await discoverAuthoredPages(authoredDirectory);
const pageCount = authoredPages.length;
const sourceRenderDirectory = path.join(qaDirectory, "source-pptx-render");
const editableRenderDirectory = path.join(qaDirectory, "editable-pptx-render");
const authoredDiffDirectory = path.join(qaDirectory, "diff-authored-vs-editable");
const annotatedDiffDirectory = path.join(qaDirectory, "diff-annotated");
const sourceDiffDirectory = path.join(qaDirectory, "diff-source-pptx-vs-editable");
const detailDirectory = path.join(qaDirectory, "slide-triptychs");
await Promise.all(
  [
    authoredDiffDirectory,
    annotatedDiffDirectory,
    sourceDiffDirectory,
    detailDirectory,
  ].map((directory) => fs.mkdir(directory, { recursive: true }))
);

const qualityPath =
  options.qualityJson ??
  `${editablePptx}.quality.json`;
const [baselineMetrics, quality, elementMap] = await Promise.all([
  readOptionalJson(options.baselineMetrics),
  readOptionalJson(qualityPath),
  readOptionalJson(options.elementMap),
]);
const fallback = summarizeFallbackQuality(quality, pageCount);

const tools = await resolvePptxRenderTools();
const [sourcePages, editablePages] = await Promise.all([
  renderPptxToPngPages({
    pptxPath: sourcePptx,
    outputDirectory: sourceRenderDirectory,
    pageCount,
    tools,
  }),
  renderPptxToPngPages({
    pptxPath: editablePptx,
    outputDirectory: editableRenderDirectory,
    pageCount,
    tools,
  }),
]);

const rows = [];
const tiles = [];
for (let index = 0; index < pageCount; index += 1) {
  const slideNumber = index + 1;
  const [authoredComparison, sourceComparison, regions] = await Promise.all([
    compareSlidePngs(authoredPages[index], editablePages[index]),
    compareSlidePngs(sourcePages[index], editablePages[index]),
    analyzeDiffRegions(authoredPages[index], editablePages[index], {
      pixelDelta: DEFAULT_FIDELITY_TOLERANCES.pixelDelta,
      maxRegions,
      elements: slideElements(elementMap, slideNumber),
    }),
  ]);
  const paddedSlideNumber = String(slideNumber).padStart(2, "0");
  const authoredDiffPath = path.join(
    authoredDiffDirectory,
    `slide-${paddedSlideNumber}.png`
  );
  const annotatedDiffPath = path.join(
    annotatedDiffDirectory,
    `slide-${paddedSlideNumber}.png`
  );
  await Promise.all([
    fs.writeFile(authoredDiffPath, authoredComparison.diffPng),
    fs.writeFile(
      path.join(sourceDiffDirectory, `slide-${paddedSlideNumber}.png`),
      sourceComparison.diffPng
    ),
    sharp(authoredComparison.diffPng)
      .composite([{ input: regionOverlay(regions), top: 0, left: 0 }])
      .png()
      .toFile(annotatedDiffPath),
  ]);

  const fallbackSlide = fallback.slides?.[index] ?? {
    rasterFallbackElements: 0,
    fallbackReasons: [],
  };
  rows.push({
    slideNumber,
    focus: options.focusSlides.includes(slideNumber),
    authoredVsEditable: {
      passed: authoredComparison.passed,
      ...authoredComparison.metrics,
    },
    sourcePptxVsEditable: {
      passed: sourceComparison.passed,
      ...sourceComparison.metrics,
    },
    diffRegions: regions,
    rasterFallbackElements: fallbackSlide.rasterFallbackElements,
    fallbackReasons: fallbackSlide.fallbackReasons,
  });

  const label = Buffer.from(
    `<svg width="384" height="28" xmlns="http://www.w3.org/2000/svg">` +
      `<rect width="384" height="28" fill="#102a56"/>` +
      `<text x="12" y="20" fill="white" font-family="Arial" font-size="16" ` +
      `font-weight="700">Slide ${slideNumber} - authored / editable / diff</text></svg>`
  );
  const tile = await sharp({
    create: {
      width: 384,
      height: 676,
      channels: 4,
      background: "#ffffff",
    },
  })
    .composite([
      { input: label, top: 0, left: 0 },
      {
        input: await sharp(authoredPages[index]).resize(384, 216).png().toBuffer(),
        top: 28,
        left: 0,
      },
      {
        input: await sharp(editablePages[index]).resize(384, 216).png().toBuffer(),
        top: 244,
        left: 0,
      },
      {
        input: await sharp(authoredComparison.diffPng)
          .resize(384, 216)
          .png()
          .toBuffer(),
        top: 460,
        left: 0,
      },
    ])
    .png()
    .toBuffer();
  tiles.push(tile);

  const detailLabel = Buffer.from(
    `<svg width="640" height="30" xmlns="http://www.w3.org/2000/svg">` +
      `<rect width="640" height="30" fill="#102a56"/>` +
      `<text x="12" y="21" fill="white" font-family="Arial" font-size="16" ` +
      `font-weight="700">Slide ${slideNumber} | MAE ${authoredComparison.metrics.meanAbsoluteError.toFixed(
        3
      )} | bad px ${(authoredComparison.metrics.badPixelRatio * 100).toFixed(
        3
      )}%</text></svg>`
  );
  await sharp({
    create: {
      width: 640,
      height: 1110,
      channels: 4,
      background: "#ffffff",
    },
  })
    .composite([
      { input: detailLabel, top: 0, left: 0 },
      {
        input: await sharp(authoredPages[index]).resize(640, 360).png().toBuffer(),
        top: 30,
        left: 0,
      },
      {
        input: await sharp(editablePages[index]).resize(640, 360).png().toBuffer(),
        top: 390,
        left: 0,
      },
      {
        input: await sharp(await fs.readFile(annotatedDiffPath))
          .resize(640, 360)
          .png()
          .toBuffer(),
        top: 750,
        left: 0,
      },
    ])
    .png()
    .toFile(path.join(detailDirectory, `slide-${paddedSlideNumber}.png`));
}

const summary = {
  authoredVsEditable: summarize(rows, "authoredVsEditable"),
  sourcePptxVsEditable: summarize(rows, "sourcePptxVsEditable"),
};
const baselineComparison = compareAgainstBaseline(
  summary.authoredVsEditable,
  rows,
  baselineMetrics
);
for (const row of rows) {
  row.baselineComparison =
    baselineComparison.slides?.find(
      (candidate) => candidate.slideNumber === row.slideNumber
    ) ?? { available: false };
}

const metrics = {
  schema: "presenton.authored-hybrid-visual-qa/v2",
  generatedAt: new Date().toISOString(),
  sourcePptx: path.resolve(sourcePptx),
  editablePptx: path.resolve(editablePptx),
  authoredDirectory: path.resolve(authoredDirectory),
  qualityJson: quality?.__path,
  elementMap: elementMap?.__path,
  pageCount,
  canvas: { width: 1280, height: 720 },
  tolerances: DEFAULT_FIDELITY_TOLERANCES,
  focusSlides: options.focusSlides,
  summary,
  baselineComparison,
  fallback,
  slides: rows,
};
await fs.writeFile(sentinel, `${JSON.stringify(metrics, null, 2)}\n`);

const columns = 5;
const rowsCount = Math.ceil(tiles.length / columns);
await sharp({
  create: {
    width: columns * 384,
    height: rowsCount * 676,
    channels: 4,
    background: "#dce7f7",
  },
})
  .composite(
    tiles.map((input, index) => ({
      input,
      left: (index % columns) * 384,
      top: Math.floor(index / columns) * 676,
    }))
  )
  .png()
  .toFile(path.join(qaDirectory, "montage-authored-editable-diff.png"));

const baselineTableColumns = baselineComparison.available
  ? " | MAE delta | Bad px delta | Verdict"
  : "";
const baselineTableSeparator = baselineComparison.available
  ? "|---:|---:|---:|"
  : "";
const table = rows
  .map((row) => {
    const baselineCells = row.baselineComparison.available
      ? ` | ${row.baselineComparison.meanAbsoluteErrorDelta.toFixed(3)} | ` +
        `${(row.baselineComparison.badPixelRatioDelta * 100).toFixed(3)} pp | ` +
        `${row.baselineComparison.classification}`
      : "";
    return (
      `| ${row.slideNumber}${row.focus ? " *" : ""} | ` +
      `${row.authoredVsEditable.meanAbsoluteError.toFixed(3)} | ` +
      `${(row.authoredVsEditable.badPixelRatio * 100).toFixed(3)}% | ` +
      `${row.authoredVsEditable.largestBadComponentPixels} | ` +
      `${row.rasterFallbackElements} | ` +
      `${row.sourcePptxVsEditable.meanAbsoluteError.toFixed(3)} | ` +
      `${(row.sourcePptxVsEditable.badPixelRatio * 100).toFixed(3)}%` +
      `${baselineCells} |`
    );
  })
  .join("\n");
const focusDetails = rows
  .filter((row) => row.focus)
  .map((row) => {
    const regions = row.diffRegions
      .slice(0, 5)
      .map(
        (region, index) =>
          `  ${index + 1}. (${region.x}, ${region.y}, ${region.width}x${region.height}), ` +
          `${region.badPixels} bad px, ${region.heuristic}` +
          (region.matchedElements.length
            ? `; elements: ${region.matchedElements
                .map((element) => `${element.id}:${element.kind}`)
                .join(", ")}`
            : "")
      )
      .join("\n");
    return (
      `### Slide ${row.slideNumber}\n\n` +
      `Fallback: ${row.rasterFallbackElements}; reasons: ` +
      `${row.fallbackReasons.join(", ") || "none reported"}\n\n${regions}`
    );
  })
  .join("\n\n");
const fallbackReasons = fallback.available
  ? Object.entries(fallback.reasonFrequency)
      .map(([reason, count]) => `- ${reason}: ${count}`)
      .join("\n")
  : "- Quality metadata was not available.";
const baselineSummary = baselineComparison.available
  ? `- Baseline verdict: ${baselineComparison.classification}\n` +
    `- Average MAE delta: ${baselineComparison.meanAbsoluteErrorDelta.toFixed(4)}\n` +
    `- Average bad-pixel delta: ${(baselineComparison.badPixelRatioDelta * 100).toFixed(
      4
    )} percentage points\n` +
    `- Regressed slides: ${baselineComparison.regressedSlides.join(", ") || "none"}\n` +
    `- Improved slides: ${baselineComparison.improvedSlides.join(", ") || "none"}`
  : "- Baseline metrics were not supplied.";
const report = `# AX editable export visual QA

- Canvas: 1280 x 720, no resampling during comparison
- Pixel delta threshold: ${DEFAULT_FIDELITY_TOLERANCES.pixelDelta}
- Authored images vs editable render passed: ${summary.authoredVsEditable.passedSlides}/${pageCount}
- Source PPTX render vs editable render passed: ${summary.sourcePptxVsEditable.passedSlides}/${pageCount}
- Average authored MAE: ${summary.authoredVsEditable.meanAbsoluteErrorAverage.toFixed(4)}
- Average authored bad-pixel ratio: ${(summary.authoredVsEditable.badPixelRatioAverage * 100).toFixed(
  4
)}%
- Asterisk marks the requested focus slides.

## Baseline comparison

${baselineSummary}

## Per-slide metrics

| Slide | Authored MAE | Authored bad px | Largest component | Raster fallback | Source PPTX MAE | Source bad px${baselineTableColumns} |
|---:|---:|---:|---:|---:|---:|---:${baselineTableSeparator}
${table}

## Raster fallback reason frequency

${fallbackReasons}

## Focus-slide diff regions

Region labels are paint-aware heuristics, not semantic ground truth. When an
element map is supplied, overlapping DOM element ids, kinds, bounds, fallback
reasons, and font observations are included in \`visual-metrics.json\`.

${focusDetails}
`;
await fs.writeFile(path.join(qaDirectory, "visual-report.md"), report);

console.log(
  JSON.stringify(
    {
      summary,
      baselineComparison: {
        available: baselineComparison.available,
        classification: baselineComparison.classification,
        meanAbsoluteErrorDelta: baselineComparison.meanAbsoluteErrorDelta,
        badPixelRatioDelta: baselineComparison.badPixelRatioDelta,
        regressedSlides: baselineComparison.regressedSlides,
        improvedSlides: baselineComparison.improvedSlides,
      },
      fallbackElements: fallback.totalRasterFallbackElements,
      outputDirectory: path.resolve(qaDirectory),
    },
    null,
    2
  )
);
if (
  options.failOnRegression &&
  baselineComparison.available &&
  baselineComparison.classification === "regressed"
) {
  process.exitCode = 2;
}
