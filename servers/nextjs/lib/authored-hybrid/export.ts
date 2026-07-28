import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { resolveAppDataDirectory } from "@/lib/app-data-directory";
import { getFastApiAuthHeaders, getFastApiBaseUrl } from "@/lib/fastapi-internal";
import {
  runBundledPresentationExport,
  type BundledPresentationExportResult,
} from "@/lib/run-bundled-presentation-export";

import {
  extractAuthoredSlideDom,
  renderAuthoredBackplate,
  resolveAuthoredHybridChromeExecutable,
} from "./index.ts";
import {
  prepareNativeElements,
  selectLayerSafeNativeElements,
  serializePreparedNativeElement,
  type PowerPointTypefaceSerializationOptions,
  type PreparedNativeElement,
} from "./native-plan.ts";
import type { AuthoredHybridTextFidelityMode } from "./text-fidelity.ts";
import {
  assembleAuthoredHybridPptx,
  type AuthoredHybridSlideLayer,
} from "./pptx-assembler.ts";
import { isAuthoredPresentation } from "./presentation-mode.ts";
import {
  preflightAuthoredHtmlForHybrid,
  readBoundedResponseText,
} from "./security.ts";
import {
  mapWithConcurrency,
  parseBoundedPositiveInt,
} from "./export-performance.ts";
import { collectGoogleFontsForAuthoredHtml } from "./google-font-collector.ts";
import { mergePowerPointTextLayout } from "./font-layout.ts";
import {
  resolveFontEmbeddingPlan,
  type FontEmbeddingPlan,
} from "./font-embedding-policy.ts";
import {
  embedPowerPointFonts,
  type PowerPointTypefaceInput,
} from "./pptx-font-embedding.ts";
import {
  createImageOnlyExportQuality,
  createPresentationExportQuality,
  type PresentationExportFontEmbeddingStatus,
  type PresentationExportFallbackElement,
  type PresentationExportQualityReport,
  type PresentationExportSlideQuality,
} from "../presentation-export-quality.ts";

const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;
const MAX_HYBRID_SLIDES = 100;
const MAX_FIDELITY_PPTX_BYTES = 512 * 1024 * 1024;
const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
// Total wall-clock budget for the bounded-concurrency per-slide hybrid pass. Each slide can
// take ~60s worst case (extract + up to 2 backplate relaunches); without a ceiling a
// pathological deck holds the export request for tens of minutes. When the budget is
// spent the remaining slides simply keep their fidelity (image) render.
const HYBRID_DECK_BUDGET_MS = 8 * 60_000;
const HYBRID_CACHE_VERSION =
  "authored-hybrid-v5-powerpoint-calibrated-text";
const HYBRID_RESULT_CACHE_LIMIT = 24;
const HYBRID_EXPORT_CONCURRENCY = parseBoundedPositiveInt(
  process.env.AUTHORED_HYBRID_CONCURRENCY,
  4,
  1,
  8
);

const completedHybridExports = new Map<
  string,
  BundledPresentationExportResult
>();
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
}

interface AuthoredHybridExportParams {
  presentationId: string;
  title?: string;
  cookieHeader?: string;
  fontEmbedding?: boolean;
  textFidelityMode?: AuthoredHybridTextFidelityMode;
}

interface FetchedPresentation {
  presentation: StoredPresentation;
  sourceSha256: string;
}

interface PreparedSlideOutcome {
  layer: AuthoredHybridSlideLayer | null;
  quality: PresentationExportSlideQuality;
  fontRendering: {
    browserFontFilesCollected: number;
    browserCollectionFailures: number;
  };
}

function imageFallbackSlide(
  slideNumber: number,
  reason: string
): PresentationExportSlideQuality {
  return {
    slideNumber,
    editable: false,
    imageFallback: true,
    nativeTextElements: 0,
    nativeShapeElements: 0,
    nativeGroupElements: 0,
    nativeImageElements: 0,
    rasterFallbackElements: 1,
    fallbackReasons: [reason],
    fallbackElements: [
      {
        elementId: `slide:${slideNumber}`,
        candidateKind: "slide",
        reasons: [reason],
      },
    ],
  };
}

function noFontRenderingTelemetry(): PreparedSlideOutcome["fontRendering"] {
  return {
    browserFontFilesCollected: 0,
    browserCollectionFailures: 0,
  };
}

function fallbackElementForContractElement(
  element: Awaited<ReturnType<typeof extractAuthoredSlideDom>>["elements"][number]
): PresentationExportFallbackElement {
  const candidateKind =
    element.classification.mode === "native"
      ? element.classification.kind
      : element.classification.candidateKind;
  return {
    elementId: element.id,
    domPath: element.domPath,
    candidateKind,
    reasons:
      element.classification.mode === "raster"
        ? element.classification.reasons
        : ["native-preparation-or-layer-safety-rejected"],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function fetchPresentation(
  presentationId: string,
  cookieHeader: string
): Promise<FetchedPresentation> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PRESENTATION_FETCH_TIMEOUT_MS);
  try {
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...getFastApiAuthHeaders(),
    };
    if (cookieHeader.trim()) headers.Cookie = cookieHeader;
    const response = await fetch(
      `${getFastApiBaseUrl()}/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}`,
      {
        method: "GET",
        headers,
        cache: "no-store",
        signal: controller.signal,
      }
    );
    if (!response.ok) {
      throw new Error(`presentation fetch returned HTTP ${response.status}`);
    }
    const body = await readBoundedResponseText(
      response,
      MAX_PRESENTATION_RESPONSE_BYTES
    );
    const parsed: unknown = JSON.parse(body);
    if (!isRecord(parsed)) throw new Error("presentation response is not an object");
    return {
      presentation: parsed as StoredPresentation,
      sourceSha256: createHash("sha256").update(body).digest("hex"),
    };
  } finally {
    clearTimeout(timer);
  }
}

function preSerializePreparedElements(
  elements: readonly PreparedNativeElement[],
  options: PowerPointTypefaceSerializationOptions = {}
): PreparedNativeElement[] {
  const serializable: PreparedNativeElement[] = [];
  for (const element of elements) {
    try {
      serializePreparedNativeElement(
        element,
        serializable.length + 3,
        element.kind === "image" ? "rId999" : undefined,
        options
      );
      serializable.push(element);
    } catch {
      // Never hide an element whose final OOXML cannot be constructed.
    }
  }
  return serializable;
}

function sameIds(
  elements: readonly PreparedNativeElement[],
  ids: readonly string[]
): boolean {
  return (
    elements.length === ids.length &&
    elements.every((element, index) => element.source.id === ids[index])
  );
}

async function prepareSlideLayer(
  html: string,
  slideNumber: number,
  chromeExecutable: string,
  embeddedTypefaceFamilies: readonly string[] = [],
  textFidelityMode?: AuthoredHybridTextFidelityMode
): Promise<PreparedSlideOutcome> {
  // Network access is permitted only in this validated server-side collector.
  // The resulting document is self-contained before isolated Chrome receives it.
  const fontCollection = await collectGoogleFontsForAuthoredHtml(html);
  const fontRendering = {
    browserFontFilesCollected: fontCollection.collectedFontFiles,
    browserCollectionFailures: fontCollection.failures.length,
  };
  const collectedHtml = fontCollection.html;
  const preflight = preflightAuthoredHtmlForHybrid(collectedHtml);
  if (!preflight.ok) {
    return {
      layer: null,
      quality: imageFallbackSlide(
        slideNumber,
        preflight.reason ?? "html-preflight-failed"
      ),
      fontRendering,
    };
  }
  const extractionOptions = { chromeExecutable, timeoutMs: 20_000 };
  const contract = await extractAuthoredSlideDom(collectedHtml, extractionOptions);
  let nativeLayoutElements = contract.elements;
  try {
    const layoutContract = await extractAuthoredSlideDom(collectedHtml, {
      ...extractionOptions,
      fontLayoutMode: "powerpoint",
    });
    const merged = mergePowerPointTextLayout(
      contract.elements,
      layoutContract.elements,
      { embeddedTypefaceFamilies }
    );
    nativeLayoutElements = merged.elements;
  } catch {
    // The mapped-font pass is an accuracy enhancement. Source extraction and
    // fidelity backplate remain a safe compatible fallback if it cannot run.
  }
  const prepared = preSerializePreparedElements(
    await prepareNativeElements(nativeLayoutElements, {
      includeRasterText: true,
      includeRasterShapes: true,
    }),
    { embeddedTypefaceFamilies, textFidelityMode }
  );
  let selected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    undefined,
    // Text remains editable above the residual backplate. Shapes only move to
    // the native layer when doing so preserves their authored stacking order;
    // otherwise translucent ancestor cards would wash out later SVG artwork.
    {
      promoteTextAboveRaster: true,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  if (!selected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(slideNumber, "no-safe-native-elements"),
      fontRendering,
    };
  }

  let backplate = await renderAuthoredBackplate(
    collectedHtml,
    contract,
    selected.map((element) => element.source.id),
    extractionOptions
  );
  const applied = new Set(backplate.appliedPromotedElementIds);
  const finalSelected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    applied,
    {
      promoteTextAboveRaster: true,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  if (!finalSelected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(slideNumber, "backplate-identity-mismatch"),
      fontRendering,
    };
  }

  if (!sameIds(finalSelected, backplate.appliedPromotedElementIds)) {
    selected = finalSelected;
    backplate = await renderAuthoredBackplate(
      collectedHtml,
      contract,
      selected.map((element) => element.source.id),
      extractionOptions
    );
    if (!sameIds(selected, backplate.appliedPromotedElementIds)) {
      return {
        layer: null,
        quality: imageFallbackSlide(slideNumber, "backplate-identity-mismatch"),
        fontRendering,
      };
    }
  } else {
    selected = finalSelected;
  }
  const selectedIds = new Set(
    selected.map((element) => element.source.id)
  );
  const fallbackElements = contract.elements
    .filter((element) => !selectedIds.has(element.id))
    .map(fallbackElementForContractElement);

  return {
    layer: {
      slideNumber,
      backplatePng: backplate.backplatePng,
      elements: selected,
    },
    quality: {
      slideNumber,
      editable: selected.length > 0,
      imageFallback: selected.length === 0,
      nativeTextElements: selected.filter((element) => element.kind === "text")
        .length,
      nativeShapeElements: selected.filter((element) => element.kind === "shape")
        .length,
      nativeGroupElements: 0,
      nativeImageElements: selected.filter((element) => element.kind === "image")
        .length,
      rasterFallbackElements: fallbackElements.length,
      fallbackReasons: fallbackElements.flatMap((element) => element.reasons),
      fallbackElements,
    },
    fontRendering,
  };
}

async function resolveSafeFidelityPptx(outPath: string): Promise<string> {
  if (path.extname(outPath).toLowerCase() !== ".pptx") {
    throw new Error("Fidelity export did not produce a PPTX file.");
  }
  const exportsDirectory = await fs.realpath(
    path.join(resolveAppDataDirectory(), "exports")
  );
  const resolved = await fs.realpath(outPath);
  if (
    resolved === exportsDirectory ||
    !resolved.startsWith(exportsDirectory + path.sep)
  ) {
    throw new Error("Fidelity export finished outside the exports directory.");
  }
  const stats = await fs.stat(resolved);
  if (!stats.isFile() || stats.size < 22 || stats.size > MAX_FIDELITY_PPTX_BYTES) {
    throw new Error("Fidelity PPTX is not a safe readable file.");
  }
  return resolved;
}

interface PreparedFontEmbedding {
  pptx: Buffer;
  status: PresentationExportFontEmbeddingStatus;
  embeddedTypefaceFamilies: readonly string[];
}

function fontEmbeddingFailures(
  plan: FontEmbeddingPlan
): NonNullable<PresentationExportFontEmbeddingStatus["failures"]> {
  return plan.status.failures.map((failure) => ({
    family: failure.family,
    ...(failure.face ? { face: failure.face } : {}),
    reason: failure.reason,
    detail: failure.detail,
  }));
}

function powerPointFontInputs(
  plan: FontEmbeddingPlan
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
  plan: FontEmbeddingPlan
): Promise<PreparedFontEmbedding> {
  const safeFidelityPath = await resolveSafeFidelityPptx(fidelityPath);
  const fidelityBytes = await fs.readFile(safeFidelityPath);
  const failures = fontEmbeddingFailures(plan);
  if (!plan.requested) {
    return {
      pptx: fidelityBytes,
      embeddedTypefaceFamilies: [],
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
    return {
      pptx: fidelityBytes,
      embeddedTypefaceFamilies: [],
      status: {
        policy: "opt-in",
        requested: true,
        applied: false,
        embeddedFontFiles: 0,
        embeddedTypefaces: 0,
        strategy: plan.strategy,
        editLimitation: "none",
        failures,
        reason: "unsupported",
      },
    };
  }

  try {
    const packaged = embedPowerPointFonts(
      fidelityBytes,
      powerPointFontInputs(plan)
    );
    if (!packaged.result.applied) {
      return {
        pptx: fidelityBytes,
        embeddedTypefaceFamilies: [],
        status: {
          policy: "opt-in",
          requested: true,
          applied: false,
          embeddedFontFiles: 0,
          embeddedTypefaces: 0,
          strategy: plan.strategy,
          editLimitation: "none",
          failures: [
            ...failures,
            {
              reason: "packaging-not-applied",
              detail:
                packaged.result.reason ??
                "The OOXML packager did not add any embedded font parts.",
            },
          ],
          reason: "failed",
        },
      };
    }

    const filesById = new Map(plan.files.map((file) => [file.sourceId, file]));
    const facesBySource = new Map(
      plan.faces.map((face) => [`${face.family}\0${face.sourceId}\0${face.face}`, face])
    );
    const faceStatus = packaged.result.fonts.flatMap((font) =>
      font.faces.map((face) => {
        const sourceId = face.source ?? "";
        const file = filesById.get(sourceId);
        const plannedFace = facesBySource.get(
          `${font.typeface}\0${sourceId}\0${face.face}`
        );
        return {
          typeface: font.typeface,
          face: face.face,
          weight: plannedFace?.weight ?? face.weight,
          style: (plannedFace?.style ??
            (face.italic ? "italic" : "normal")) as "normal" | "italic",
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
          ...(file?.derivedFromVariable
            ? { derivedFromVariable: true }
            : {}),
        };
      })
    );
    const embeddedTypefaceFamilies = packaged.result.fonts.map(
      (font) => font.typeface
    );
    return {
      pptx: packaged.pptx,
      embeddedTypefaceFamilies,
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
    return {
      pptx: fidelityBytes,
      embeddedTypefaceFamilies: [],
      status: {
        policy: "opt-in",
        requested: true,
        applied: false,
        embeddedFontFiles: 0,
        embeddedTypefaces: 0,
        strategy: plan.strategy,
        editLimitation: "none",
        failures: [
          ...failures,
          {
            reason: "packaging-failed",
            detail: error instanceof Error ? error.message : String(error),
          },
        ],
        reason: "failed",
      },
    };
  }
}

async function writeHybridPptx(
  fidelityPath: string,
  layers: readonly AuthoredHybridSlideLayer[],
  basePptx?: Buffer,
  embeddedTypefaceFamilies: readonly string[] = [],
  textFidelityMode?: AuthoredHybridTextFidelityMode
): Promise<string> {
  const safeFidelityPath = await resolveSafeFidelityPptx(fidelityPath);
  const fidelityBytes = basePptx ?? (await fs.readFile(safeFidelityPath));
  const hybridBytes = assembleAuthoredHybridPptx(fidelityBytes, layers, {
    embeddedTypefaceFamilies,
    textFidelityMode,
  });
  const parsed = path.parse(safeFidelityPath);
  const suffix = randomUUID();
  const hybridPath = path.join(parsed.dir, `${parsed.name}-hybrid-${suffix}.pptx`);
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

async function attachQualityReport(
  result: BundledPresentationExportResult,
  quality: PresentationExportQualityReport
): Promise<BundledPresentationExportResult> {
  const qualityPath = `${result.path}.quality.json`;
  const temporaryPath = `${qualityPath}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporaryPath, JSON.stringify(quality, null, 2), {
      flag: "wx",
      mode: 0o600,
    });
    await fs.rename(temporaryPath, qualityPath);
    await fs.chmod(qualityPath, 0o644);
  } catch (error) {
    console.warn("[authored-hybrid] quality sidecar could not be persisted", error);
    await fs.rm(temporaryPath, { force: true }).catch(() => {});
  }
  return { ...result, quality };
}

async function runUncachedAuthoredHybridExport(
  params: AuthoredHybridExportParams,
  presentation: StoredPresentation,
  sourceSha256: string,
  fontEmbeddingPlan: FontEmbeddingPlan,
  cacheKey: string,
  requestStartedAt: number
): Promise<BundledPresentationExportResult> {
  const fidelityStartedAt = performance.now();
  const fidelity = await runBundledPresentationExport({
    format: "pptx",
    presentationId: params.presentationId,
    title: params.title,
    cookieHeader: params.cookieHeader,
    expectedPresentationSha256: sourceSha256,
  });
  const fidelityMs = elapsedMs(fidelityStartedAt);
  const fontEmbedding = await prepareFontEmbedding(
    fidelity.path,
    fontEmbeddingPlan
  );

  try {
    const slides = Array.isArray(presentation.slides) ? presentation.slides : [];
    const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
    if (!chromeExecutable) {
      console.info(
        `[authored-hybrid] fidelity-only total=${elapsedMs(requestStartedAt)}ms ` +
          `fidelity=${fidelityMs}ms reason=chrome-unavailable`
      );
      return attachQualityReport(
        fontEmbedding.status.applied
          ? {
              path: await writeHybridPptx(
                fidelity.path,
                [],
                fontEmbedding.pptx,
                fontEmbedding.embeddedTypefaceFamilies
              ),
            }
          : fidelity,
        createImageOnlyExportQuality(slides.length, "chrome-unavailable", {
          fontEmbeddingStatus: fontEmbedding.status,
        })
      );
    }

    const slideInputs = slides
      .map((rawSlide, arrayIndex) => {
        if (!isRecord(rawSlide)) {
          return { html: "", slideNumber: arrayIndex + 1 };
        }
        const slide = rawSlide as StoredSlide;
        const html = typeof slide.html_content === "string" ? slide.html_content : "";
        return { html, slideNumber: arrayIndex + 1 };
      });

    const deckStart = Date.now();
    const slidesStartedAt = performance.now();
    let budgetWarningWritten = false;
    const preparedSlides = await mapWithConcurrency(
      slideInputs,
      HYBRID_EXPORT_CONCURRENCY,
      async ({ html, slideNumber }) => {
        if (Date.now() - deckStart > HYBRID_DECK_BUDGET_MS) {
          if (!budgetWarningWritten) {
            budgetWarningWritten = true;
            console.warn(
              "[authored-hybrid] deck time budget exhausted; remaining slides " +
                "keep the fidelity render"
            );
          }
          return {
            layer: null,
            quality: imageFallbackSlide(
              slideNumber,
              "deck-time-budget-exhausted"
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
            params.textFidelityMode
          );
        } catch (error) {
          console.warn(
            `[authored-hybrid:slide-${slideNumber}] fidelity fallback after slide processing failure`,
            error
          );
          return {
            layer: null,
            quality: imageFallbackSlide(
              slideNumber,
              "slide-processing-failed"
            ),
            fontRendering: noFontRenderingTelemetry(),
          };
        }
      }
    );
    const layers = preparedSlides
      .map((prepared) => prepared.layer)
      .filter(
        (layer): layer is AuthoredHybridSlideLayer => layer !== null
      );
    const quality = createPresentationExportQuality(
      "hybrid",
      preparedSlides.map((prepared) => prepared.quality),
      {
        fontEmbeddingStatus: fontEmbedding.status,
        fontRendering: {
          browserFontFilesCollected: preparedSlides.reduce(
            (sum, prepared) =>
              sum + prepared.fontRendering.browserFontFilesCollected,
            0
          ),
          browserCollectionFailures: preparedSlides.reduce(
            (sum, prepared) =>
              sum + prepared.fontRendering.browserCollectionFailures,
            0
          ),
        },
      }
    );
    const slidesMs = elapsedMs(slidesStartedAt);
    if (!layers.length && !fontEmbedding.status.applied) {
      return attachQualityReport(fidelity, quality);
    }
    const assemblyStartedAt = performance.now();
    const hybridPath = await writeHybridPptx(
      fidelity.path,
      layers,
      fontEmbedding.pptx,
      fontEmbedding.embeddedTypefaceFamilies,
      params.textFidelityMode
    );
    const assemblyMs = elapsedMs(assemblyStartedAt);
    const result = await attachQualityReport({ path: hybridPath }, quality);
    rememberCompletedHybridExport(cacheKey, result);
    console.info(
      `[authored-hybrid] complete total=${elapsedMs(requestStartedAt)}ms ` +
        `fidelity=${fidelityMs}ms slides=${slidesMs}ms assembly=${assemblyMs}ms ` +
        `editable=${layers.length}/${slides.length} ` +
        `concurrency=${HYBRID_EXPORT_CONCURRENCY}`
    );
    return result;
  } catch (error) {
    console.warn(
      "[authored-hybrid] fidelity fallback after hybrid processing failure",
      error
    );
    const slideCount = Array.isArray(presentation.slides)
      ? presentation.slides.length
      : 0;
    return attachQualityReport(
      fontEmbedding.status.applied
        ? {
            path: await writeHybridPptx(
              fidelity.path,
              [],
              fontEmbedding.pptx,
              fontEmbedding.embeddedTypefaceFamilies
            ),
          }
        : fidelity,
      createImageOnlyExportQuality(slideCount, "hybrid-processing-failed", {
        fontEmbeddingStatus: fontEmbedding.status,
      })
    );
  }
}

/**
 * Export authored decks with editable native layers. Identical completed exports
 * are reused while this server process is alive, and concurrent duplicate requests
 * share one export job instead of spawning duplicate Chrome processes.
 */
export async function runAuthoredHybridPresentationExport(
  params: AuthoredHybridExportParams
): Promise<BundledPresentationExportResult> {
  const requestStartedAt = performance.now();
  let fetched: FetchedPresentation;
  try {
    fetched = await fetchPresentation(
      params.presentationId,
      params.cookieHeader ?? ""
    );
  } catch (error) {
    console.warn(
      "[authored-hybrid] presentation lookup failed; using fidelity export",
      error
    );
    return runBundledPresentationExport({
      format: "pptx",
      presentationId: params.presentationId,
      title: params.title,
      cookieHeader: params.cookieHeader,
    });
  }

  const { presentation } = fetched;
  if (!isAuthoredPresentation(presentation) || !Array.isArray(presentation.slides)) {
    return runBundledPresentationExport({
      format: "pptx",
      presentationId: params.presentationId,
      title: params.title,
      cookieHeader: params.cookieHeader,
    });
  }
  if (
    presentation.slides.length === 0 ||
    presentation.slides.length > MAX_HYBRID_SLIDES
  ) {
    return runBundledPresentationExport({
      format: "pptx",
      presentationId: params.presentationId,
      title: params.title,
      cookieHeader: params.cookieHeader,
    });
  }

  const fontEmbeddingPlan = await resolveFontEmbeddingPlan({
    requested: params.fontEmbedding === true,
    families: ["Noto Sans KR"],
    // Full-font embedding keeps newly typed Hangul editable. The opt-in API can
    // add subset selection later without changing this safe default.
    strategy: "full",
    allowVariableFonts: false,
  });
  const cacheKey = createHybridCacheKey(
    params.presentationId,
    fetched.sourceSha256,
    fontEmbeddingPlan.cacheDiscriminator,
    params.textFidelityMode
  );
  const cached = await readCompletedHybridExport(cacheKey);
  if (cached) {
    console.info(
      `[authored-hybrid] cache-hit total=${elapsedMs(requestStartedAt)}ms`
    );
    return cached;
  }

  const inFlight = inFlightHybridExports.get(cacheKey);
  if (inFlight) {
    console.info("[authored-hybrid] joined existing export job");
    return inFlight;
  }

  const job = runUncachedAuthoredHybridExport(
    params,
    presentation,
    fetched.sourceSha256,
    fontEmbeddingPlan,
    cacheKey,
    requestStartedAt
  ).finally(() => {
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
  fontEmbeddingDiscriminator: string,
  textFidelityMode?: AuthoredHybridTextFidelityMode
): string {
  return createHash("sha256")
    .update(HYBRID_CACHE_VERSION)
    .update("\0")
    .update(presentationId)
    .update("\0")
    .update(sourceSha256)
    .update("\0")
    .update(fontEmbeddingDiscriminator)
    .update("\0")
    .update(textFidelityMode ?? "editable-default")
    .digest("hex");
}

function rememberCompletedHybridExport(
  cacheKey: string,
  result: BundledPresentationExportResult
): void {
  completedHybridExports.delete(cacheKey);
  completedHybridExports.set(cacheKey, result);
  while (completedHybridExports.size > HYBRID_RESULT_CACHE_LIMIT) {
    const oldestKey = completedHybridExports.keys().next().value;
    if (typeof oldestKey !== "string") break;
    completedHybridExports.delete(oldestKey);
  }
}

async function readCompletedHybridExport(
  cacheKey: string
): Promise<BundledPresentationExportResult | null> {
  const cached = completedHybridExports.get(cacheKey);
  if (!cached) return null;
  try {
    const safePath = await resolveSafeFidelityPptx(cached.path);
    completedHybridExports.delete(cacheKey);
    const safeResult = { ...cached, path: safePath };
    completedHybridExports.set(cacheKey, safeResult);
    return safeResult;
  } catch {
    completedHybridExports.delete(cacheKey);
    return null;
  }
}

function elapsedMs(startedAt: number): number {
  return Math.round(performance.now() - startedAt);
}
