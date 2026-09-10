/**
 * Sessionless authored-hybrid assembler for the isolated public export worker.
 *
 * The public export lane never reaches Next.js: the isolated export worker
 * builds the fidelity raster deck, then spawns this process with digest
 * verified scratch paths. Chrome and the OOXML assembler therefore run inside
 * the isolated worker's job object, never in the durable operation, Core or
 * API process (decision `public-export.hybrid-in-isolated-lane`).
 *
 * Wire: one `public-hybrid-export-job/v1` object on stdin, one
 * `public-hybrid-export-result/v1` object on stdout. No database, no network,
 * no session material crosses this boundary.
 */

import { readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { installNextjsModuleAliasHooks } from "./lib/nextjs-module-alias-hooks.mjs";

const JOB_SCHEMA = "public-hybrid-export-job/v1";
const RESULT_SCHEMA = "public-hybrid-export-result/v1";
const MAX_SLIDES = 100;
const MAX_JOB_BYTES = 1024 * 1024;
const MAX_BASE_PPTX_BYTES = 512 * 1024 * 1024;
const KOREAN_NATIVE_EMBEDDING_FAMILY = "Noto Sans KR";
const KOREAN_FACE_PATTERN = /(?:noto\s+sans\s+(?:kr|cjk)|pretendard|malgun|맑은\s*고딕)/iu;
const DIGEST = /^sha256:[0-9a-f]{64}$/;

class HybridWorkerError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

async function readJob() {
  const chunks = [];
  let total = 0;
  for await (const chunk of process.stdin) {
    total += chunk.length;
    if (total > MAX_JOB_BYTES) throw new HybridWorkerError("hybrid_job_wire_limit");
    chunks.push(chunk);
  }
  let value;
  try {
    value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new HybridWorkerError("hybrid_job_invalid_json");
  }
  if (
    value === null ||
    typeof value !== "object" ||
    value.schemaVersion !== JOB_SCHEMA ||
    typeof value.basePptxPath !== "string" ||
    typeof value.outputPath !== "string" ||
    typeof value.scratchDirectory !== "string" ||
    !Array.isArray(value.slides) ||
    value.slides.length < 1 ||
    value.slides.length > MAX_SLIDES ||
    typeof value.fontEmbedding !== "boolean"
  ) {
    throw new HybridWorkerError("hybrid_job_invalid_shape");
  }
  const slides = value.slides.map((slide, index) => {
    if (
      slide === null ||
      typeof slide !== "object" ||
      slide.slideIndex !== index ||
      typeof slide.htmlPath !== "string" ||
      !path.isAbsolute(slide.htmlPath)
    ) {
      throw new HybridWorkerError("hybrid_job_slide_invalid");
    }
    return { slideNumber: index + 1, htmlPath: slide.htmlPath };
  });
  if (
    !path.isAbsolute(value.basePptxPath) ||
    !path.isAbsolute(value.outputPath) ||
    !path.isAbsolute(value.scratchDirectory)
  ) {
    throw new HybridWorkerError("hybrid_job_path_not_absolute");
  }
  const concurrency = Number.isSafeInteger(value.concurrency)
    ? Math.max(1, Math.min(4, value.concurrency))
    : 2;
  const budgetMillis = Number.isSafeInteger(value.budgetMillis)
    ? Math.max(30_000, Math.min(30 * 60_000, value.budgetMillis))
    : 10 * 60_000;
  const fontAllowlistDirectory =
    typeof value.fontAllowlistDirectory === "string" && value.fontAllowlistDirectory
      ? value.fontAllowlistDirectory
      : null;
  if (fontAllowlistDirectory !== null && !path.isAbsolute(fontAllowlistDirectory)) {
    throw new HybridWorkerError("hybrid_job_path_not_absolute");
  }
  // The declared semantic graph is what admits groups, tables and charts. It
  // is optional: without it the lane still assembles an observed-only deck.
  const exportAttemptId =
    typeof value.exportAttemptId === "string" && value.exportAttemptId
      ? value.exportAttemptId
      : null;
  const rendererSourceDigest =
    typeof value.rendererSourceDigest === "string" && DIGEST.test(value.rendererSourceDigest)
      ? value.rendererSourceDigest
      : null;
  const semanticSlides = Array.isArray(value.semanticSlides) ? value.semanticSlides : [];
  if (semanticSlides.length) {
    if (
      semanticSlides.length !== slides.length ||
      !exportAttemptId ||
      !rendererSourceDigest
    ) {
      throw new HybridWorkerError("hybrid_job_semantic_binding_incomplete");
    }
    semanticSlides.forEach((declared, index) => {
      if (
        declared === null ||
        typeof declared !== "object" ||
        declared.slideIndex !== index ||
        !Array.isArray(declared.declaredElementIds) ||
        !Array.isArray(declared.declaredElements) ||
        !DIGEST.test(declared.bundleDigest ?? "") ||
        !DIGEST.test(declared.fontSetDigest ?? "")
      ) {
        throw new HybridWorkerError("hybrid_job_semantic_invalid");
      }
    });
  }
  return {
    basePptxPath: value.basePptxPath,
    outputPath: value.outputPath,
    scratchDirectory: value.scratchDirectory,
    fontAllowlistDirectory,
    fontEmbedding: value.fontEmbedding,
    exportAttemptId,
    rendererSourceDigest,
    semanticSlides,
    slides,
    concurrency,
    budgetMillis,
  };
}

/** Import the authored-hybrid modules after the `@/` alias hooks exist. */
async function loadHybridModules() {
  installNextjsModuleAliasHooks();
  const libRoot = pathToFileURL(
    path.join(path.resolve(path.join(import.meta.dirname, "..")), "lib", path.sep),
  ).href;
  const [index, slideLayer, assembler, fontEmbedding, fontPolicy, quality, inspection, slideQuality, localFit] =
    await Promise.all([
      import(`${libRoot}authored-hybrid/index.ts`),
      import(`${libRoot}authored-hybrid/slide-layer.ts`),
      import(`${libRoot}authored-hybrid/pptx-assembler.ts`),
      import(`${libRoot}authored-hybrid/export-font-embedding.ts`),
      import(`${libRoot}authored-hybrid/font-embedding-policy.ts`),
      import(`${libRoot}presentation-export-quality.ts`),
      import(`${libRoot}authored-hybrid/pptx-quality-inspection.ts`),
      import(`${libRoot}authored-hybrid/export-slide-quality.ts`),
      import(`${libRoot}authored-hybrid/local-fit-export-evidence.ts`),
    ]);
  return {
    resolveAuthoredHybridChromeExecutable: index.resolveAuthoredHybridChromeExecutable,
    prepareSlideLayer: slideLayer.prepareSlideLayer,
    assembleAuthoredHybridPptxAsync: assembler.assembleAuthoredHybridPptxAsync,
    prepareFontEmbedding: fontEmbedding.prepareFontEmbedding,
    embeddedGlyphAvailable: fontEmbedding.embeddedGlyphAvailable,
    bundledFamiliesReferencedByHtml: fontEmbedding.bundledFamiliesReferencedByHtml,
    resolveFontEmbeddingPlan: fontPolicy.resolveFontEmbeddingPlan,
    createPresentationExportQuality: quality.createPresentationExportQuality,
    inspectPptxEditability: inspection.inspectPptxEditability,
    imageFallbackSlide: slideQuality.imageFallbackSlide,
    noFontRenderingTelemetry: slideQuality.noFontRenderingTelemetry,
    createLocalFitExportEvidence: localFit.createLocalFitExportEvidence,
    writeLocalFitExportEvidence: localFit.writeLocalFitExportEvidence,
  };
}

async function mapWithBudget(items, concurrency, deadline, callback, onExpired) {
  const output = new Array(items.length);
  let next = 0;
  async function pump() {
    while (next < items.length) {
      const index = next++;
      output[index] =
        Date.now() >= deadline ? onExpired(items[index]) : await callback(items[index]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, pump));
  return output;
}

function summarizeStructural(structural) {
  const slides = structural.slides.map((slide) => ({
    n: slide.slideNumber,
    text: slide.nativeTextElements ?? 0,
    shape: slide.nativeShapeElements ?? 0,
    group: slide.nativeGroupElements ?? 0,
    image: slide.nativeImageElements ?? 0,
    chart: slide.nativeChartElements ?? 0,
    table: slide.nativeTableElements ?? 0,
    backplates: slide.backplateCount ?? 0,
    fullSlideRaster: (slide.rasterFallbackElements ?? 0) > 0,
    readback: slide.ooxmlReadback?.status ?? "unknown",
  }));
  const sum = (key) => slides.reduce((total, slide) => total + slide[key], 0);
  return {
    slideCount: slides.length,
    nativeLayerSlides: slides.filter((slide) => slide.backplates > 0).length,
    fullSlideRasterSlides: slides.filter((slide) => slide.fullSlideRaster).length,
    readbackFailedSlides: slides.filter((slide) => slide.readback === "failed").length,
    nativeTextElements: sum("text"),
    nativeShapeElements: sum("shape"),
    nativeGroupElements: sum("group"),
    nativeImageElements: sum("image"),
    nativeChartElements: sum("chart"),
    nativeTableElements: sum("table"),
    slides,
  };
}

function summarizeQuality(report) {
  const tally = {};
  for (const slide of report.slides) {
    for (const reason of slide.fallbackReasons ?? []) {
      tally[reason] = (tally[reason] ?? 0) + 1;
    }
  }
  return {
    status: report.status,
    editableSlides: report.slides.filter((slide) => slide.editable).length,
    imageFallbackSlides: report.slides.filter((slide) => slide.imageFallback).length,
    rasterFallbackElements: report.slides.reduce(
      (total, slide) => total + (slide.rasterFallbackElements ?? 0),
      0,
    ),
    fallbackReasonTally: tally,
  };
}

async function run() {
  const job = await readJob();
  // Bundled fonts ship beside the Next.js server; the worker's cwd is its own
  // sandbox, so point the reader at the repository copy explicitly. App data
  // is redirected into the job scratch: collection is disabled, so nothing
  // durable is read or written there.
  process.env.PRESENTON_APP_ROOT ??= path.resolve(path.join(import.meta.dirname, "..", "..", ".."));
  process.env.APP_DATA_DIRECTORY = job.scratchDirectory;
  process.env.DISABLE_GOOGLE_FONTS_COLLECTION = "true";
  // Licensed OOXML embedding reads a static, read-only server font allowlist.
  // The isolated worker receives that one directory explicitly instead of the
  // whole app-data tree.
  if (job.fontAllowlistDirectory) {
    process.env.PRESENTON_FONT_EMBEDDING_DIR = job.fontAllowlistDirectory;
  }

  const modules = await loadHybridModules();
  const chromeExecutable = await modules.resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) throw new HybridWorkerError("hybrid_chrome_unavailable");

  const baseInfo = await stat(job.basePptxPath);
  if (!baseInfo.isFile() || baseInfo.size <= 0 || baseInfo.size > MAX_BASE_PPTX_BYTES) {
    throw new HybridWorkerError("hybrid_base_pptx_invalid");
  }
  const basePptx = await readFile(job.basePptxPath);
  const htmls = await Promise.all(
    job.slides.map((slide) => readFile(slide.htmlPath, "utf8")),
  );
  const semantics = job.slides.map(
    (_slide, index) => job.semanticSlides[index] ?? null,
  );

  const fontPlan = await modules.resolveFontEmbeddingPlan({
    requested: job.fontEmbedding,
    // Browser CSS often names only the Latin face while Hangul resolves through
    // a fallback, so a Hangul-capable deck requests the Korean package face as
    // well as the bundled families its markup names.
    families: [
      ...new Set([
        ...(KOREAN_FACE_PATTERN.test(htmls.join("\n")) ? [KOREAN_NATIVE_EMBEDDING_FAMILY] : []),
        ...modules.bundledFamiliesReferencedByHtml(htmls),
      ]),
    ],
    strategy: "full",
    allowVariableFonts: false,
  });
  let embedding;
  try {
    embedding = await modules.prepareFontEmbedding(basePptx, fontPlan);
  } catch (error) {
    // A font without a redistribution licence, or an OOXML packaging refusal,
    // must never cost the user their export. Continue unembedded and say so.
    embedding = {
      pptx: basePptx,
      embeddedTypefaceFamilies: [],
      embeddedTypefaceFaces: [],
      status: {
        policy: "opt-in",
        requested: job.fontEmbedding,
        applied: false,
        embeddedFontFiles: 0,
        embeddedTypefaces: 0,
        strategy: "full",
        editLimitation: "none",
        reason: error instanceof Error ? error.message : "font-embedding-failed",
      },
    };
  }

  const deadline = Date.now() + job.budgetMillis;
  const glyphAvailable = modules.embeddedGlyphAvailable(fontPlan);
  const outcomes = await mapWithBudget(
    job.slides.map((slide, index) => ({
      ...slide,
      html: htmls[index],
      semantic: semantics[index],
    })),
    job.concurrency,
    deadline,
    async (slide) => {
      const semanticContext = slide.semantic
        ? {
            exportAttemptId: job.exportAttemptId,
            inputBundleDigest: slide.semantic.bundleDigest,
            rendererSourceDigest: job.rendererSourceDigest,
            fontSetDigest: slide.semantic.fontSetDigest,
            declaredElementIds: slide.semantic.declaredElementIds,
            declaredElements: slide.semantic.declaredElements,
          }
        : undefined;
      try {
        return await modules.prepareSlideLayer(
          slide.html,
          slide.slideNumber,
          chromeExecutable,
          embedding.embeddedTypefaceFamilies,
          embedding.embeddedTypefaceFaces,
          semanticContext,
          glyphAvailable,
        );
      } catch (error) {
        // A declared graph that does not reconcile with what the browser
        // observed costs this slide its native layer, never the export. The
        // reason is distinct so the receipt says which failure it was.
        const semanticMismatch =
          semanticContext !== undefined &&
          error instanceof Error &&
          /semantic/i.test(error.name + error.message);
        process.stderr.write(
          `[public-hybrid] slide ${slide.slideNumber} raster fallback: ${String(error)}\n`,
        );
        return {
          layer: null,
          quality: modules.imageFallbackSlide(
            slide.slideNumber,
            semanticMismatch
              ? "semantic-observation-mismatch"
              : "slide-processing-failed",
          ),
          fontRendering: modules.noFontRenderingTelemetry(),
        };
      }
    },
    (slide) => ({
      layer: null,
      quality: modules.imageFallbackSlide(slide.slideNumber, "hybrid-deck-budget-exhausted"),
      fontRendering: modules.noFontRenderingTelemetry(),
    }),
  );

  const layers = outcomes.map((outcome) => outcome.layer).filter(Boolean);
  const report = modules.createPresentationExportQuality(
    "hybrid",
    outcomes.map((outcome) => outcome.quality),
    {
      fontEmbeddingStatus: embedding.status,
      fontRendering: {
        browserFontFilesCollected: outcomes.reduce(
          (total, outcome) => total + outcome.fontRendering.browserFontFilesCollected,
          0,
        ),
        browserCollectionFailures: outcomes.reduce(
          (total, outcome) => total + outcome.fontRendering.browserCollectionFailures,
          0,
        ),
      },
    },
  );
  if (!layers.length) {
    // Every slide fell back. Report why, per slide: a bare "no layers" leaves
    // the caller with a downgrade it can name but cannot explain or act on.
    const noLayers = new HybridWorkerError("hybrid_no_native_layers");
    noLayers.quality = summarizeQuality(report);
    throw noLayers;
  }
  const hybridBytes = await modules.assembleAuthoredHybridPptxAsync(
    embedding.pptx,
    layers,
    { embeddedTypefaceFamilies: embedding.embeddedTypefaceFamilies },
  );
  await writeFile(job.outputPath, hybridBytes, { flag: "wx", mode: 0o600 });
  await modules.writeLocalFitExportEvidence(job.outputPath,
    modules.createLocalFitExportEvidence(layers, htmls, job.rendererSourceDigest ?? "", hybridBytes));
  const structural = modules.inspectPptxEditability(hybridBytes);

  return {
    schemaVersion: RESULT_SCHEMA,
    ok: true,
    errorCode: null,
    quality: summarizeQuality(report),
    structural: summarizeStructural(structural),
    semantic: {
      slidesWithDeclaredGraph: semantics.filter(Boolean).length,
      declaredElements: semantics.reduce(
        (total, declared) => total + (declared ? declared.declaredElements.length : 0),
        0,
      ),
    },
    fontEmbedding: {
      requested: embedding.status.requested === true,
      applied: embedding.status.applied === true,
      embeddedTypefaces: embedding.status.embeddedTypefaces ?? 0,
      reason: embedding.status.reason ?? null,
    },
  };
}

try {
  const result = await run();
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exit(0);
} catch (error) {
  const code =
    error instanceof HybridWorkerError
      ? error.code
      : `hybrid_unexpected_failure:${error instanceof Error ? error.message.slice(0, 120) : "unknown"}`;
  process.stdout.write(
    `${JSON.stringify({
      schemaVersion: RESULT_SCHEMA,
      ok: false,
      errorCode: code,
      // A refusal still owes an account of itself when it has one.
      ...(error instanceof HybridWorkerError && error.quality
        ? { quality: error.quality }
        : {}),
    })}\n`,
  );
  process.exit(1);
}
