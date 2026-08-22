import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";

import { resolveAppDataDirectory } from "@/lib/app-data-directory";
import {
  getFastApiAuthHeaders,
  getFastApiBaseUrl,
} from "@/lib/fastapi-internal";
import {
  runBundledPresentationExport,
  type BundledPresentationExportResult,
} from "@/lib/run-bundled-presentation-export";
import { fetchPersistedPresentationProjectionForExport } from "@/lib/export-presentation-route";

import { resolveAuthoredHybridChromeExecutable } from "./index.ts";
import {
  assembleAuthoredHybridPptx,
  type AuthoredHybridSlideLayer,
  type AuthoredHybridPptxProvenance,
} from "./pptx-assembler.ts";
import { isAuthoredPresentation } from "./presentation-mode.ts";
import { authoredRendererIsolationPolicy } from "./renderer-isolation.ts";
import {
  beginExportProgress,
  recordExportProgress,
} from "./export-progress.ts";
import {
  prepareFidelitySlideLayer,
  prepareSlideLayer,
} from "./slide-layer.ts";
import {
  mapWithConcurrency,
  parseBoundedPositiveInt,
  resolveHybridExportConcurrencyDefault,
} from "./export-performance.ts";
import { attachAuthoredHybridEvidence } from "./export-evidence.ts";
import {
  resolveFontEmbeddingPlan,
  type FontEmbeddingPlan,
} from "./font-embedding-policy.ts";
import {
  embedPowerPointFonts,
  type PowerPointTypefaceInput,
} from "./pptx-font-embedding.ts";
import {
  embeddedFaceForStyle,
  resolvePowerPointTypeface,
  type AuthoredEmbeddedTypefaceFaceEvidence,
} from "./font-policy.ts";
import {
  attachPresentationExportOoxmlReadback,
  createPresentationExportQuality,
  type PresentationExportFontEmbeddingStatus,
  type PresentationExportRendererIsolation,
} from "../presentation-export-quality.ts";
import { inspectPptxEditability } from "./pptx-quality-inspection.ts";
import {
  imageFallbackSlide,
  noFontRenderingTelemetry,
} from "./export-slide-quality.ts";

const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;
const MAX_HYBRID_SLIDES = 100;
const MAX_FIDELITY_PPTX_BYTES = 512 * 1024 * 1024;
const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
// Total wall-clock budget for the bounded-concurrency per-slide hybrid pass. Each slide can
// take ~90s worst case (extract + font-layout + backplate relaunches); without a ceiling a
// pathological deck holds the export request for tens of minutes. When the budget is
// spent the remaining slides receive a separately isolated fidelity capture below. Never
// retain the bundled export's shared-DOM raster: authored slide styles can collide there.
const HYBRID_DECK_BUDGET_MS = 15 * 60_000;
const HYBRID_CACHE_VERSION = "authored-hybrid-v15-isolated-raster-fallback";
const HYBRID_RESULT_CACHE_LIMIT = 24;
const HYBRID_EXPORT_CONCURRENCY = parseBoundedPositiveInt(
  process.env.AUTHORED_HYBRID_CONCURRENCY,
  resolveHybridExportConcurrencyDefault(),
  1,
  12,
);

interface CompletedHybridExport {
  result: BundledPresentationExportResult;
  artifactSha256: string;
  artifactSizeBytes: number;
}

const completedHybridExports = new Map<string, CompletedHybridExport>();
const inFlightHybridExports = new Map<
  string,
  Promise<BundledPresentationExportResult>
>();

interface StoredSlide {
  index?: unknown;
  layout_group?: unknown;
  html_content?: unknown;
  content?: unknown;
}

interface StoredPresentation {
  mode?: unknown;
  theme?: unknown;
  slides?: unknown;
  revision?: unknown;
}

export interface AuthoredHybridExportParams {
  presentationId: string;
  title?: string;
  cookieHeader?: string;
  fontEmbedding?: boolean;
  expectedPresentationRevision?: number;
  /** Correlates this export with the caller polling /api/internal/export-progress. */
  operationId?: string;
  /** Durable FastAPI attempt identity; optional only at the shared type boundary. */
  exportAttemptId?: string;
  canonicalSourceDigest?: string;
}

interface FetchedPresentation {
  presentation: StoredPresentation;
  sourceSha256: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Stamp every quality report with the isolation the render actually ran under,
 * so an unattested local-native artifact stays identifiable downstream. A policy
 * that throws is reported as unattested rather than crashing the export: the
 * per-slide passes surface that failure on their own.
 */
function rendererIsolationRecord(): PresentationExportRendererIsolation {
  try {
    const policy = authoredRendererIsolationPolicy();
    return {
      osIsolation: policy.productionOsIsolation,
      executionBoundary: policy.executionBoundary,
      attested:
        policy.productionOsIsolation === "nonroot-readonly-tmpfs-network-none",
    };
  } catch {
    return {
      osIsolation: "development-unattested",
      executionBoundary: "development-local",
      attested: false,
    };
  }
}

async function fetchPresentation(
  presentationId: string,
  cookieHeader: string,
): Promise<FetchedPresentation> {
  const fetched = await fetchPersistedPresentationProjectionForExport(
    presentationId,
    {
      baseUrl: getFastApiBaseUrl(),
      authHeaders: getFastApiAuthHeaders(),
      cookieHeader,
      timeoutMs: PRESENTATION_FETCH_TIMEOUT_MS,
      maxResponseBytes: MAX_PRESENTATION_RESPONSE_BYTES,
    },
  );
  return {
    presentation: fetched.presentation as StoredPresentation,
    sourceSha256: fetched.sourceSha256,
  };
}

async function resolveSafeFidelityPptx(outPath: string): Promise<string> {
  if (path.extname(outPath).toLowerCase() !== ".pptx") {
    throw new Error("Fidelity export did not produce a PPTX file.");
  }
  const exportsDirectory = await fs.realpath(
    path.join(resolveAppDataDirectory(), "exports"),
  );
  const resolved = await fs.realpath(outPath);
  if (
    resolved === exportsDirectory ||
    !resolved.startsWith(exportsDirectory + path.sep)
  ) {
    throw new Error("Fidelity export finished outside the exports directory.");
  }
  const stats = await fs.stat(resolved);
  if (
    !stats.isFile() ||
    stats.size < 22 ||
    stats.size > MAX_FIDELITY_PPTX_BYTES
  ) {
    throw new Error("Fidelity PPTX is not a safe readable file.");
  }
  return resolved;
}

interface PreparedFontEmbedding {
  pptx: Buffer;
  status: PresentationExportFontEmbeddingStatus;
  embeddedTypefaceFamilies: readonly string[];
  embeddedTypefaceFaces: readonly AuthoredEmbeddedTypefaceFaceEvidence[];
}

function normalizedTypeface(value: string): string {
  return value.trim().replace(/^['"]|['"]$/g, "").toLocaleLowerCase("en-US");
}

function nativeFontUsage(
  layers: readonly AuthoredHybridSlideLayer[],
  embedding: PreparedFontEmbedding,
): Pick<
  PresentationExportFontEmbeddingStatus,
  | "nativeTextUsage"
  | "nativeTextElements"
  | "nativeTextElementsUsingEmbeddedTypefaces"
  | "nativeTypefaceFamilies"
> {
  const textElements = layers.flatMap((layer) =>
    layer.elements.filter((element) => element.kind === "text"),
  );
  const usedFamilies = new Set<string>();
  let usingEmbedded = 0;
  for (const element of textElements) {
    const usesEmbedded = element.source.text.runs.some((run) => {
      const face = embeddedFaceForStyle(run.style);
      const faceFamilies = new Set(
        embedding.embeddedTypefaceFaces
          .filter((entry) => entry.face === face)
          .map((entry) => entry.typeface),
      );
      if (!faceFamilies.size) return false;
      const normalizedEmbedded = new Set(
        [...faceFamilies].map(normalizedTypeface),
      );
      const latin = resolvePowerPointTypeface(run.style.fontFamilies, "Aptos", {
        preserveAuthoredFamilies: faceFamilies,
      });
      const eastAsian = normalizedEmbedded.has(normalizedTypeface(latin))
        ? latin
        : resolvePowerPointTypeface(
            run.style.cjkFallbackFamilies,
            process.platform === "win32" ? "Malgun Gothic" : "Noto Sans CJK KR",
            { preserveAuthoredFamilies: faceFamilies },
          );
      for (const typeface of [latin, eastAsian]) {
        if (normalizedEmbedded.has(normalizedTypeface(typeface))) {
          usedFamilies.add(typeface);
          return true;
        }
      }
      return false;
    });
    if (usesEmbedded) usingEmbedded += 1;
  }
  const nativeTextElements = textElements.length;
  const nativeTextUsage = !embedding.status.applied
    ? "not-embedded"
    : nativeTextElements === 0
      ? "raster-only-unused"
      : usingEmbedded > 0
        ? "used"
        : "not-used";
  return {
    nativeTextUsage,
    nativeTextElements,
    nativeTextElementsUsingEmbeddedTypefaces: usingEmbedded,
    nativeTypefaceFamilies: [...usedFamilies].sort(),
  };
}

function fontEmbeddingFailures(
  plan: FontEmbeddingPlan,
): NonNullable<PresentationExportFontEmbeddingStatus["failures"]> {
  return plan.status.failures.map((failure) => ({
    family: failure.family,
    ...(failure.face ? { face: failure.face } : {}),
    reason: failure.reason,
    detail: failure.detail,
  }));
}

function powerPointFontInputs(
  plan: FontEmbeddingPlan,
): PowerPointTypefaceInput[] {
  const filesById = new Map(plan.files.map((file) => [file.sourceId, file]));
  const byFamily = new Map<
    string,
    Partial<
      Record<
        "regular" | "bold" | "italic" | "boldItalic",
        { data: Buffer; subset: boolean; source: string }
      >
    >
  >();
  for (const face of plan.faces) {
    const file = filesById.get(face.sourceId);
    if (!file) continue;
    const faces = byFamily.get(face.family) ?? {};
    faces[face.face] = {
      data: file.data,
      subset: plan.strategy === "subset",
      source: file.sourceId,
    };
    byFamily.set(face.family, faces);
  }
  return [...byFamily.entries()].map(([typeface, faces]) => ({
    typeface,
    // Hangul charset and a variable/sans-serif pitch-family value. These values
    // match Office's normal declaration for a Korean sans-serif typeface.
    pitchFamily: 34,
    charset: 129,
    faces,
  }));
}

async function prepareFontEmbedding(
  fidelityPath: string,
  plan: FontEmbeddingPlan,
): Promise<PreparedFontEmbedding> {
  const safeFidelityPath = await resolveSafeFidelityPptx(fidelityPath);
  const fidelityBytes = await fs.readFile(safeFidelityPath);
  const failures = fontEmbeddingFailures(plan);
  if (!plan.requested) {
    return {
      pptx: fidelityBytes,
      embeddedTypefaceFamilies: [],
      embeddedTypefaceFaces: [],
      status: {
        policy: "opt-in",
        requested: false,
        applied: false,
        embeddedFontFiles: 0,
        embeddedTypefaces: 0,
        strategy: plan.strategy,
        editLimitation: "none",
        reason: "not-requested",
      },
    };
  }
  if (!plan.status.eligible) {
    throw new Error("authored_hybrid_font_embedding_ineligible");
  }

  try {
    const packaged = embedPowerPointFonts(
      fidelityBytes,
      powerPointFontInputs(plan),
    );
    if (!packaged.result.applied) {
      throw new Error("authored_hybrid_font_embedding_not_applied");
    }

    const filesById = new Map(plan.files.map((file) => [file.sourceId, file]));
    const facesBySource = new Map(
      plan.faces.map((face) => [
        `${face.family}\0${face.sourceId}\0${face.face}`,
        face,
      ]),
    );
    const faceStatus = packaged.result.fonts.flatMap((font) =>
      font.faces.map((face) => {
        const sourceId = face.source ?? "";
        const file = filesById.get(sourceId);
        const plannedFace = facesBySource.get(
          `${font.typeface}\0${sourceId}\0${face.face}`,
        );
        return {
          typeface: font.typeface,
          face: face.face,
          weight: plannedFace?.weight ?? face.weight,
          style: (plannedFace?.style ?? (face.italic ? "italic" : "normal")) as
            "normal" | "italic",
          source: file?.source ?? "server-font-allowlist",
          ...(file?.sourcePath ? { sourcePath: file.sourcePath } : {}),
          sourceSha256: face.sourceSha256,
          sourceBytes: face.sourceBytes,
          embeddedBytes: face.embeddedBytes,
          fsType: face.fsType,
          licenseDecision:
            file?.license.decision ?? ("denied-invalid" as const),
          subset: face.subset,
          strategy: plan.strategy,
          partName: face.partName,
          format: face.format,
          ...(file?.derivedFromVariable ? { derivedFromVariable: true } : {}),
        };
      }),
    );
    const embeddedTypefaceFamilies = packaged.result.fonts.map(
      (font) => font.typeface,
    );
    return {
      pptx: packaged.pptx,
      embeddedTypefaceFamilies,
      embeddedTypefaceFaces: faceStatus.map((face) => ({
        typeface: face.typeface,
        face: face.face,
      })),
      status: {
        policy: "opt-in",
        requested: true,
        applied: true,
        embeddedFontFiles: packaged.result.embeddedFontFiles,
        embeddedTypefaces: packaged.result.embeddedTypefaces,
        strategy: plan.strategy,
        editLimitation:
          plan.strategy === "subset"
            ? "characters-outside-subset-may-substitute"
            : "none",
        faces: faceStatus,
        ...(failures.length ? { failures } : {}),
        reason: "embedded",
      },
    };
  } catch (error) {
    if (
      error instanceof Error &&
      error.message === "authored_hybrid_font_embedding_not_applied"
    ) {
      throw error;
    }
    throw new Error("authored_hybrid_font_embedding_failed", { cause: error });
  }
}

async function writeHybridPptx(
  fidelityPath: string,
  layers: readonly AuthoredHybridSlideLayer[],
  basePptx?: Buffer,
  embeddedTypefaceFamilies: readonly string[] = [],
  artifactKind: "hybrid" | "authored-fidelity" = "hybrid",
  provenance?: AuthoredHybridPptxProvenance,
): Promise<string> {
  const safeFidelityPath = await resolveSafeFidelityPptx(fidelityPath);
  const fidelityBytes = basePptx ?? (await fs.readFile(safeFidelityPath));
  const hybridBytes = assembleAuthoredHybridPptx(fidelityBytes, layers, {
    embeddedTypefaceFamilies,
    provenance,
  });
  const parsed = path.parse(safeFidelityPath);
  const suffix = randomUUID();
  const hybridPath = path.join(
    parsed.dir,
    `${parsed.name}-${artifactKind}-${suffix}.pptx`,
  );
  const temporaryPath = path.join(parsed.dir, `.${parsed.name}-${suffix}.tmp`);
  let published = false;
  try {
    await fs.writeFile(temporaryPath, hybridBytes, { flag: "wx", mode: 0o600 });
    await fs.rename(temporaryPath, hybridPath);
    published = true;
    await fs.chmod(hybridPath, 0o644);
  } catch (error) {
    if (published) await fs.rm(hybridPath, { force: true }).catch(() => {});
    throw error;
  } finally {
    await fs.rm(temporaryPath, { force: true }).catch(() => {});
  }
  // Keep the fidelity PPTX: it is a normal export artifact at a title-derived path,
  // so a previously issued download link (or a concurrent export reading it) must
  // not 404 just because a hybrid variant was written alongside it.
  return hybridPath;
}

/**
 * Authored fidelity is an exact, full-slide raster capture from the same
 * revision-sealed browser source used by Hybrid. No native element is emitted.
 */
export async function runAuthoredFidelityPresentationExport(
  params: Omit<AuthoredHybridExportParams, "fontEmbedding" | "operationId">,
): Promise<BundledPresentationExportResult> {
  const fetched = await fetchPresentation(
    params.presentationId,
    params.cookieHeader ?? "",
  );
  const { presentation } = fetched;
  if (
    !Number.isSafeInteger(presentation.revision) ||
    (presentation.revision as number) < 0 ||
    (params.expectedPresentationRevision !== undefined &&
      presentation.revision !== params.expectedPresentationRevision) ||
    !isAuthoredPresentation(presentation) ||
    !Array.isArray(presentation.slides) ||
    presentation.slides.length === 0 ||
    presentation.slides.length > MAX_HYBRID_SLIDES
  ) {
    throw new Error("authored_fidelity_source_invalid");
  }

  const fidelity = await runBundledPresentationExport({
    format: "pptx",
    presentationId: params.presentationId,
    title: params.title,
    cookieHeader: params.cookieHeader,
    expectedPresentationSha256: fetched.sourceSha256,
    expectedPresentationRevision: params.expectedPresentationRevision,
  });
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) throw new Error("authored_fidelity_chrome_unavailable");

  const layers = await mapWithConcurrency(
    presentation.slides,
    HYBRID_EXPORT_CONCURRENCY,
    async (rawSlide, arrayIndex) => {
      if (!isRecord(rawSlide) || typeof rawSlide.html_content !== "string") {
        throw new Error("authored_fidelity_slide_html_required");
      }
      const prepared = await prepareFidelitySlideLayer(
        rawSlide.html_content,
        arrayIndex + 1,
        chromeExecutable,
      );
      if (!prepared.layer) throw new Error("authored_fidelity_slide_capture_failed");
      return prepared.layer;
    },
  );
  const resultPath = await writeHybridPptx(
    fidelity.path,
    layers,
    undefined,
    [],
    "authored-fidelity",
  );
  return { path: resultPath };
}

async function runUncachedAuthoredHybridExport(
  params: AuthoredHybridExportParams,
  presentation: StoredPresentation,
  sourceSha256: string,
  fontEmbeddingPlan: FontEmbeddingPlan,
  cacheKey: string,
  requestStartedAt: number,
): Promise<BundledPresentationExportResult> {
  const fidelityStartedAt = performance.now();
  recordExportProgress(cacheKey, { phase: "fidelity" });
  const fidelity = await runBundledPresentationExport({
    format: "pptx",
    presentationId: params.presentationId,
    title: params.title,
    cookieHeader: params.cookieHeader,
    expectedPresentationSha256: sourceSha256,
    expectedPresentationRevision: params.expectedPresentationRevision,
  });
  const fidelityMs = elapsedMs(fidelityStartedAt);
  const fontEmbedding = await prepareFontEmbedding(
    fidelity.path,
    fontEmbeddingPlan,
  );

  try {
    const slides = Array.isArray(presentation.slides)
      ? presentation.slides
      : [];
    const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
    if (!chromeExecutable) {
      console.info(
        `[authored-hybrid] fidelity-only total=${elapsedMs(requestStartedAt)}ms ` +
          `fidelity=${fidelityMs}ms reason=chrome-unavailable`,
      );
      throw new Error("authored_hybrid_chrome_unavailable");
    }

    const slideInputs = slides.map((rawSlide, arrayIndex) => {
      if (!isRecord(rawSlide)) {
        return { html: "", slideNumber: arrayIndex + 1 };
      }
      const slide = rawSlide as StoredSlide;
      const html =
        typeof slide.html_content === "string" ? slide.html_content : "";
      return { html, slideNumber: arrayIndex + 1 };
    });

    const deckStart = Date.now();
    const slidesStartedAt = performance.now();
    let budgetWarningWritten = false;
    let completedSlides = 0;
    recordExportProgress(cacheKey, {
      phase: "slides",
      completedSlides: 0,
      totalSlides: slideInputs.length,
    });
    // Slides finish concurrently and a 100-slide deck would otherwise append a
    // timeline event per slide. Publishing on a fixed stride keeps the poller
    // meaningful without turning progress into its own write load.
    const progressStride = Math.max(1, Math.ceil(slideInputs.length / 40));
    const noteSlideCompleted = () => {
      completedSlides += 1;
      if (
        completedSlides % progressStride === 0 ||
        completedSlides === slideInputs.length
      ) {
        recordExportProgress(cacheKey, { completedSlides });
      }
    };
    const preparedSlides = await mapWithConcurrency(
      slideInputs,
      HYBRID_EXPORT_CONCURRENCY,
      async ({ html, slideNumber }) => {
        try {
          if (Date.now() - deckStart > HYBRID_DECK_BUDGET_MS) {
            if (!budgetWarningWritten) {
              budgetWarningWritten = true;
              console.warn(
                "[authored-hybrid] deck time budget exhausted; remaining slides " +
                  "use isolated fidelity captures",
              );
            }
            return {
              layer: null,
              quality: imageFallbackSlide(
                slideNumber,
                "deck-time-budget-exhausted",
              ),
              fontRendering: noFontRenderingTelemetry(),
            };
          }
          try {
            return await prepareSlideLayer(
              html,
              slideNumber,
              chromeExecutable,
              fontEmbedding.embeddedTypefaceFamilies,
              fontEmbedding.embeddedTypefaceFaces,
            );
          } catch (error) {
            console.warn(
              `[authored-hybrid:slide-${slideNumber}] fidelity fallback after slide processing failure`,
              error,
            );
            return {
              layer: null,
              quality: imageFallbackSlide(
                slideNumber,
                "slide-processing-failed",
              ),
              fontRendering: noFontRenderingTelemetry(),
            };
          }
        } finally {
          // A slide that fell back is still finished work; counting it keeps
          // progress monotonic instead of stalling on the failing slides.
          noteSlideCompleted();
        }
      },
    );
    // Typography failures are element-scoped. Do not discard safe native
    // shapes or text on unrelated slides merely because another candidate did
    // not survive preflight; only a slide with no safe native layer receives an
    // isolated full-slide raster capture.
    const rasterFallbackInputs = slideInputs.filter(
      ({ slideNumber }) => preparedSlides[slideNumber - 1]?.layer === null,
    );
    const isolatedRasterLayers = await mapWithConcurrency(
      rasterFallbackInputs,
      HYBRID_EXPORT_CONCURRENCY,
      async ({ html, slideNumber }) => {
        const isolated = await prepareFidelitySlideLayer(
          html,
          slideNumber,
          chromeExecutable,
        );
        if (!isolated.layer) {
          throw new Error("authored_hybrid_isolated_raster_fallback_failed");
        }
        return isolated.layer;
      },
    );
    const isolatedRasterBySlide = new Map(
      isolatedRasterLayers.map((layer) => [layer.slideNumber, layer] as const),
    );
    const layers = slideInputs.map(({ slideNumber }) => {
      const prepared = preparedSlides[slideNumber - 1]?.layer;
      const layer = prepared ?? isolatedRasterBySlide.get(slideNumber);
      if (!layer) {
        throw new Error("authored_hybrid_slide_layer_missing");
      }
      return layer;
    });
    const qualitySlides = preparedSlides.map((prepared) =>
      prepared.layer
        ? prepared.quality
        : {
            ...prepared.quality,
            backplateCount: 1,
            fallbackReasons: [
              ...prepared.quality.fallbackReasons,
              "isolated-fidelity-raster-fallback",
            ],
          },
    );
    const logicalQuality = createPresentationExportQuality(
      "hybrid",
      qualitySlides,
      {
        fontEmbeddingStatus: {
          ...fontEmbedding.status,
          ...nativeFontUsage(layers, fontEmbedding),
        },
        rendererIsolation: rendererIsolationRecord(),
        fontRendering: {
          browserFontFilesCollected: preparedSlides.reduce(
            (sum, prepared) =>
              sum + prepared.fontRendering.browserFontFilesCollected,
            0,
          ),
          browserCollectionFailures: preparedSlides.reduce(
            (sum, prepared) =>
              sum + prepared.fontRendering.browserCollectionFailures,
            0,
          ),
        },
      },
    );
    const slidesMs = elapsedMs(slidesStartedAt);
    const assemblyStartedAt = performance.now();
    recordExportProgress(cacheKey, { phase: "assembly" });
    if (!params.exportAttemptId || !params.canonicalSourceDigest) {
      throw new Error("authored_hybrid_export_attempt_binding_required");
    }
    const hybridPath = await writeHybridPptx(
      fidelity.path,
      layers,
      fontEmbedding.pptx,
      fontEmbedding.embeddedTypefaceFamilies,
      "hybrid",
      {
        exportAttemptId: params.exportAttemptId,
        presentationRevision: Number(presentation.revision),
        canonicalSourceDigest: params.canonicalSourceDigest,
        sourceDigest: sourceSha256,
      },
    );
    const assemblyMs = elapsedMs(assemblyStartedAt);
    const hybridBytes = await fs.readFile(hybridPath);
    const quality = attachPresentationExportOoxmlReadback(
      logicalQuality,
      inspectPptxEditability(hybridBytes),
    );
    const result = await attachAuthoredHybridEvidence({ path: hybridPath }, quality, {
      exportAttemptId: params.exportAttemptId,
      presentationId: params.presentationId,
      presentationRevision: Number(presentation.revision),
      canonicalSourceDigest: params.canonicalSourceDigest,
      sourceDigest: sourceSha256,
      pptxSha256: createHash("sha256").update(hybridBytes).digest("hex"),
    });
    await rememberCompletedHybridExport(cacheKey, result);
    console.info(
      `[authored-hybrid] complete total=${elapsedMs(requestStartedAt)}ms ` +
        `fidelity=${fidelityMs}ms slides=${slidesMs}ms assembly=${assemblyMs}ms ` +
        `editable=${layers.filter((layer) => layer.elements.length > 0).length}/${slides.length} ` +
        `concurrency=${HYBRID_EXPORT_CONCURRENCY}`,
    );
    return result;
  } catch (error) {
    console.warn("[authored-hybrid] explicit hybrid export failed", error);
    throw error;
  }
}

/**
 * Export authored decks with editable native layers. Identical completed exports
 * are reused while this server process is alive, and concurrent duplicate requests
 * share one export job instead of spawning duplicate Chrome processes.
 */
export async function runAuthoredHybridPresentationExport(
  params: AuthoredHybridExportParams,
): Promise<BundledPresentationExportResult> {
  const requestStartedAt = performance.now();
  let fetched: FetchedPresentation;
  try {
    fetched = await fetchPresentation(
      params.presentationId,
      params.cookieHeader ?? "",
    );
  } catch (error) {
    throw new Error("authored_hybrid_presentation_lookup_failed", {
      cause: error,
    });
  }

  const { presentation } = fetched;
  if (
    !Number.isSafeInteger(presentation.revision) ||
    (presentation.revision as number) < 0
  ) {
    throw new Error("authored_hybrid_revision_invalid");
  }
  if (
    params.expectedPresentationRevision !== undefined &&
    presentation.revision !== params.expectedPresentationRevision
  ) {
    throw new Error("stale_render_revision");
  }
  if (
    !isAuthoredPresentation(presentation) ||
    !Array.isArray(presentation.slides)
  ) {
    throw new Error("authored_hybrid_requires_authored_presentation");
  }
  if (
    presentation.slides.length === 0 ||
    presentation.slides.length > MAX_HYBRID_SLIDES
  ) {
    throw new Error("authored_hybrid_slide_count_unsupported");
  }

  const fontEmbeddingPlan = await resolveFontEmbeddingPlan({
    requested: params.fontEmbedding !== false,
    families: ["Noto Sans KR"],
    // Full-font embedding keeps newly typed Hangul editable. The opt-in API can
    // add subset selection later without changing this safe default.
    strategy: "full",
    allowVariableFonts: false,
  });
  const cacheKey = createHybridCacheKey(
    params.presentationId,
    fetched.sourceSha256,
    Number.isSafeInteger(presentation.revision)
      ? (presentation.revision as number)
      : params.expectedPresentationRevision,
    fontEmbeddingPlan.cacheDiscriminator,
    params.exportAttemptId,
    params.canonicalSourceDigest,
  );
  const cached = await readCompletedHybridExport(cacheKey);
  if (cached) {
    console.info(
      `[authored-hybrid] cache-hit total=${elapsedMs(requestStartedAt)}ms`,
    );
    return cached;
  }

  const inFlight = inFlightHybridExports.get(cacheKey);
  if (inFlight) {
    console.info("[authored-hybrid] joined existing export job");
    // Point this operation at the running job's progress instead of starting a
    // second record, so a joining caller reports the work that is actually
    // happening rather than sitting silent until the shared job resolves.
    beginExportProgress(cacheKey, params.operationId, Date.now());
    return inFlight;
  }

  beginExportProgress(cacheKey, params.operationId, Date.now());
  const job = runUncachedAuthoredHybridExport(
    params,
    presentation,
    fetched.sourceSha256,
    fontEmbeddingPlan,
    cacheKey,
    requestStartedAt,
  )
    .then(
      (result) => {
        recordExportProgress(cacheKey, { phase: "completed" });
        return result;
      },
      (error: unknown) => {
        recordExportProgress(cacheKey, { phase: "failed" });
        throw error;
      },
    )
    .finally(() => {
      if (inFlightHybridExports.get(cacheKey) === job) {
        inFlightHybridExports.delete(cacheKey);
      }
    });
  inFlightHybridExports.set(cacheKey, job);
  return job;
}

function createHybridCacheKey(
  presentationId: string,
  sourceSha256: string,
  sourceRevision: number | undefined,
  fontEmbeddingDiscriminator: string,
  exportAttemptId: string | undefined,
  canonicalSourceDigest: string | undefined,
): string {
  return createHash("sha256")
    .update(HYBRID_CACHE_VERSION)
    .update("\0")
    .update(presentationId)
    .update("\0")
    .update(sourceSha256)
    .update("\0")
    .update(sourceRevision === undefined ? "unknown" : String(sourceRevision))
    .update("\0")
    .update(fontEmbeddingDiscriminator)
    .update("\0")
    .update(exportAttemptId ?? "unbound")
    .update("\0")
    .update(canonicalSourceDigest ?? "unbound")
    .digest("hex");
}

async function readHybridArtifactIdentity(
  artifactPath: string,
): Promise<{ sha256: string; sizeBytes: number }> {
  const safePath = await resolveSafeFidelityPptx(artifactPath);
  const stats = await fs.stat(safePath);
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(safePath)) {
    digest.update(chunk);
  }
  return { sha256: digest.digest("hex"), sizeBytes: stats.size };
}

async function rememberCompletedHybridExport(
  cacheKey: string,
  result: BundledPresentationExportResult,
): Promise<void> {
  const identity = await readHybridArtifactIdentity(result.path);
  completedHybridExports.delete(cacheKey);
  completedHybridExports.set(cacheKey, {
    result,
    artifactSha256: identity.sha256,
    artifactSizeBytes: identity.sizeBytes,
  });
  while (completedHybridExports.size > HYBRID_RESULT_CACHE_LIMIT) {
    const oldestKey = completedHybridExports.keys().next().value;
    if (typeof oldestKey !== "string") break;
    completedHybridExports.delete(oldestKey);
  }
}

async function readCompletedHybridExport(
  cacheKey: string,
): Promise<BundledPresentationExportResult | null> {
  const cached = completedHybridExports.get(cacheKey);
  if (!cached) return null;
  try {
    const safePath = await resolveSafeFidelityPptx(cached.result.path);
    const identity = await readHybridArtifactIdentity(safePath);
    if (
      identity.sha256 !== cached.artifactSha256 ||
      identity.sizeBytes !== cached.artifactSizeBytes
    ) {
      throw new Error("cached_hybrid_artifact_digest_mismatch");
    }
    completedHybridExports.delete(cacheKey);
    const safeResult = { ...cached.result, path: safePath };
    completedHybridExports.set(cacheKey, { ...cached, result: safeResult });
    return safeResult;
  } catch {
    completedHybridExports.delete(cacheKey);
    return null;
  }
}

function elapsedMs(startedAt: number): number {
  return Math.round(performance.now() - startedAt);
}
