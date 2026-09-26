import path from "node:path";

const JOB_SCHEMA = "public-hybrid-export-job/v1";
const MAX_SLIDES = 100;
// The parent already admits and stages export bundles at an 8 MiB boundary.
// A 100-slide declared semantic graph can legitimately exceed 1 MiB even
// though every slide and artifact reference has passed that outer admission.
const MAX_JOB_BYTES = 8 * 1024 * 1024;
const DIGEST = /^sha256:[0-9a-f]{64}$/;

export class HybridWorkerError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

export async function readJob() {
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
    canvas: value.canvas,
    exportAttemptId,
    rendererSourceDigest,
    semanticSlides,
    slides,
    concurrency,
    budgetMillis,
  };
}

/** Import the authored-hybrid modules after the `@/` alias hooks exist. */
