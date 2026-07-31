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
  type PreparedNativeElement,
} from "./native-plan.ts";
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
import {
  resolveFontEmbeddingPlan,
  type FontEmbeddingPlan,
} from "./font-embedding-policy.ts";
import {
  embedPowerPointFonts,
  type PowerPointTypefaceInput,
} from "./pptx-font-embedding.ts";
import {
  summarizeAuthoredHybridExport,
  type AuthoredHybridSlideOutcome,
} from "./export-metadata.ts";
import { GoogleFontsCollector } from "./google-fonts-collector.ts";

const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;
const MAX_HYBRID_SLIDES = 100;
const MAX_FIDELITY_PPTX_BYTES = 512 * 1024 * 1024;
const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
// Total wall-clock budget for the bounded-concurrency per-slide hybrid pass. Each slide can
// take ~135s worst case (extract + up to 2 backplate relaunches); without a ceiling a
// pathological deck holds the export request for tens of minutes. When the budget is
// spent the remaining slides simply keep their fidelity (image) render.
const HYBRID_DECK_BUDGET_MS = 8 * 60_000;
const HYBRID_BROWSER_PASS_TIMEOUT_MS = 45_000;
const HYBRID_CACHE_VERSION = "authored-hybrid-v10-semantic-scenes-45s";
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
const googleFontsCollector = new GoogleFontsCollector({
  cacheDirectory: path.join(
    resolveAppDataDirectory(),
    "authored-hybrid",
    "google-fonts"
  ),
});

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
}

interface FetchedPresentation {
  presentation: StoredPresentation;
  sourceSha256: string;
}

interface PreparedSlideLayerResult extends AuthoredHybridSlideOutcome {
  layer: AuthoredHybridSlideLayer | null;
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
  elements: readonly PreparedNativeElement[]
): PreparedNativeElement[] {
  const serializable: PreparedNativeElement[] = [];
  for (const element of elements) {
    try {
      serializePreparedNativeElement(
        element,
        serializable.length + 3,
        element.kind === "image" ? "rId999" : undefined
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
  embeddedTypefaceFamilies: readonly string[] = []
): Promise<PreparedSlideLayerResult> {
  // Only the collector may cross this trust boundary. Its sanitized form is
  // checked by the existing preflight; its verified data-font form is used
  // only after that succeeds. Chrome itself remains fully offline.
  const collectedFonts = await googleFontsCollector.prepareHtml(html);
  const preflight = preflightAuthoredHtmlForHybrid(
    collectedFonts.htmlForPreflight
  );
  if (!preflight.ok) {
    return {
      editable: false,
      fallbackReason: `preflight-${preflight.reason}`,
      layer: null,
    };
  }
  const htmlForChrome = collectedFonts.htmlForChrome;
  const extractionOptions = {
    chromeExecutable,
    timeoutMs: HYBRID_BROWSER_PASS_TIMEOUT_MS,
  };
  const contract = await extractAuthoredSlideDom(
    htmlForChrome,
    extractionOptions
  );
  const prepared = preSerializePreparedElements(
    await prepareNativeElements(contract.elements, {
      includeRasterText: true,
      includeRasterShapes: true,
      // A locally packaged font is as safe to preserve in the native text
      // layer as a font verified by the Google Fonts collector. Without this
      // union, offline/authored decks are embedded with Noto Sans KR while the
      // editable runs are rewritten to Malgun Gothic, so PowerPoint never uses
      // the embedded face and line wrapping drifts from the fidelity render.
      trustedFontFamilies: [
        ...new Set([
          ...collectedFonts.trustedFamilies,
          ...embeddedTypefaceFamilies,
        ]),
      ],
    })
  );
  let selected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    undefined,
    // Text remains editable above the residual backplate. Shapes only move to
    // the native layer when doing so preserves their authored stacking order;
    // otherwise translucent ancestor cards would wash out later SVG artwork.
    { promoteTextAboveRaster: true, promoteShapesAboveRaster: false }
  );
  if (!selected.length) {
    return {
      editable: false,
      fallbackReason: "no-editable-elements",
      layer: null,
    };
  }

  let backplate = await renderAuthoredBackplate(
    htmlForChrome,
    contract,
    selected.map((element) => element.source.id),
    extractionOptions
  );
  const applied = new Set(backplate.appliedPromotedElementIds);
  const finalSelected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    applied,
    { promoteTextAboveRaster: true, promoteShapesAboveRaster: false }
  );
  if (!finalSelected.length) {
    return {
      editable: false,
      fallbackReason: "no-layer-safe-elements",
      layer: null,
    };
  }

  if (!sameIds(finalSelected, backplate.appliedPromotedElementIds)) {
    selected = finalSelected;
    backplate = await renderAuthoredBackplate(
      htmlForChrome,
      contract,
      selected.map((element) => element.source.id),
      extractionOptions
    );
    if (!sameIds(selected, backplate.appliedPromotedElementIds)) {
      return {
        editable: false,
        fallbackReason: "promotion-mismatch",
        layer: null,
      };
    }
  } else {
    selected = finalSelected;
  }

  return {
    editable: true,
    layer: {
      slideNumber,
      backplatePng: backplate.backplatePng,
      elements: selected,
    },
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
      source: file.sourcePath,
    };
    byFamily.set(face.family, faces);
  }
  return [...byFamily.entries()].map(([typeface, faces]) => ({
    typeface,
    pitchFamily: 34,
    charset: 129,
    faces,
  }));
}

async function embedAuthoredFonts(
  fidelityPath: string,
  plan: FontEmbeddingPlan
): Promise<Buffer | null> {
  if (!plan.status.eligible) return null;
  try {
    const safeFidelityPath = await resolveSafeFidelityPptx(fidelityPath);
    const fidelityBytes = await fs.readFile(safeFidelityPath);
    const packaged = embedPowerPointFonts(
      fidelityBytes,
      powerPointFontInputs(plan)
    );
    return packaged.result.applied ? packaged.pptx : null;
  } catch (error) {
    console.warn(
      "[authored-hybrid] automatic font embedding failed; continuing without embedded fonts",
      error
    );
    return null;
  }
}

async function writeHybridPptx(
  fidelityPath: string,
  layers: readonly AuthoredHybridSlideLayer[],
  preparedFidelityBytes?: Buffer
): Promise<string> {
  const safeFidelityPath = await resolveSafeFidelityPptx(fidelityPath);
  const fidelityBytes =
    preparedFidelityBytes ?? (await fs.readFile(safeFidelityPath));
  const hybridBytes = assembleAuthoredHybridPptx(fidelityBytes, layers);
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
  const embeddedFidelityBytes = await embedAuthoredFonts(
    fidelity.path,
    fontEmbeddingPlan
  );

  try {
    const slides = Array.isArray(presentation.slides) ? presentation.slides : [];
    const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
    if (!chromeExecutable) {
      const exportMetadata = summarizeAuthoredHybridExport(
        slides.map(() => ({
          editable: false,
          fallbackReason: "chrome-unavailable",
        }))
      );
      console.info(
        `[authored-hybrid] fidelity-only total=${elapsedMs(requestStartedAt)}ms ` +
          `fidelity=${fidelityMs}ms reason=chrome-unavailable`
      );
      if (!embeddedFidelityBytes) return { ...fidelity, exportMetadata };
      return {
        path: await writeHybridPptx(fidelity.path, [], embeddedFidelityBytes),
        exportMetadata,
      };
    }

    const slideInputs = slides
      .map((rawSlide, arrayIndex) => {
        if (!isRecord(rawSlide)) {
          return {
            html: null,
            slideNumber: arrayIndex + 1,
            fallbackReason: "invalid-slide",
          };
        }
        const slide = rawSlide as StoredSlide;
        const html = typeof slide.html_content === "string" ? slide.html_content : "";
        return {
          html: html || null,
          slideNumber: arrayIndex + 1,
          fallbackReason: html ? undefined : "missing-html",
        };
      });

    const deckStart = Date.now();
    const slidesStartedAt = performance.now();
    let budgetWarningWritten = false;
    const preparedLayers = await mapWithConcurrency(
      slideInputs,
      HYBRID_EXPORT_CONCURRENCY,
      async ({ html, slideNumber, fallbackReason }) => {
        if (!html) {
          return {
            editable: false,
            fallbackReason: fallbackReason ?? "missing-html",
            layer: null,
          } satisfies PreparedSlideLayerResult;
        }
        if (Date.now() - deckStart > HYBRID_DECK_BUDGET_MS) {
          if (!budgetWarningWritten) {
            budgetWarningWritten = true;
            console.warn(
              "[authored-hybrid] deck time budget exhausted; remaining slides " +
                "keep the fidelity render"
            );
          }
          return {
            editable: false,
            fallbackReason: "deck-time-budget-exhausted",
            layer: null,
          } satisfies PreparedSlideLayerResult;
        }
        try {
          return await prepareSlideLayer(
            html,
            slideNumber,
            chromeExecutable,
            [...fontEmbeddingPlan.eligibleFamilies]
          );
        } catch (error) {
          console.warn(
            `[authored-hybrid:slide-${slideNumber}] fidelity fallback after slide processing failure`,
            error
          );
          return {
            editable: false,
            fallbackReason: "slide-processing-failed",
            layer: null,
          } satisfies PreparedSlideLayerResult;
        }
      }
    );
    const layers = preparedLayers
      .map((result) => result.layer)
      .filter(
        (layer): layer is AuthoredHybridSlideLayer => layer !== null
      );
    const exportMetadata = summarizeAuthoredHybridExport(
      preparedLayers.map(({ editable, fallbackReason }) => ({
        editable,
        fallbackReason,
      }))
    );
    const slidesMs = elapsedMs(slidesStartedAt);
    if (!layers.length) {
      console.info(
        `[authored-hybrid] fidelity-only total=${elapsedMs(requestStartedAt)}ms ` +
          `fidelity=${fidelityMs}ms slides=${slidesMs}ms ` +
          `editable=0/${slides.length} reasons=${JSON.stringify(exportMetadata.fallbackReasons)}`
      );
      if (!embeddedFidelityBytes) return { ...fidelity, exportMetadata };
      return {
        path: await writeHybridPptx(fidelity.path, [], embeddedFidelityBytes),
        exportMetadata,
      };
    }
    const assemblyStartedAt = performance.now();
    const hybridPath = await writeHybridPptx(
      fidelity.path,
      layers,
      embeddedFidelityBytes ?? undefined
    );
    const assemblyMs = elapsedMs(assemblyStartedAt);
    const result = { path: hybridPath, exportMetadata };
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
    const slides = Array.isArray(presentation.slides) ? presentation.slides : [];
    const exportMetadata = summarizeAuthoredHybridExport(
      slides.map(() => ({
        editable: false,
        fallbackReason: "hybrid-processing-failed",
      }))
    );
    if (embeddedFidelityBytes) {
      return {
        path: await writeHybridPptx(fidelity.path, [], embeddedFidelityBytes),
        exportMetadata,
      };
    }
    return {
      ...fidelity,
      exportMetadata,
    };
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
  const fontEmbeddingPlan = await resolveFontEmbeddingPlan({
    requested: true,
    families: ["Noto Sans KR"],
    strategy: "full",
    allowVariableFonts: false,
  });
  if (
    presentation.slides.length === 0 ||
    presentation.slides.length > MAX_HYBRID_SLIDES
  ) {
    const fidelity = await runBundledPresentationExport({
      format: "pptx",
      presentationId: params.presentationId,
      title: params.title,
      cookieHeader: params.cookieHeader,
    });
    if (presentation.slides.length === 0) return fidelity;
    return {
      ...fidelity,
      exportMetadata: summarizeAuthoredHybridExport(
        presentation.slides.map(() => ({
          editable: false,
          fallbackReason: "slide-limit-exceeded",
        }))
      ),
    };
  }

  const cacheKey = createHybridCacheKey(
    params.presentationId,
    fetched.sourceSha256,
    fontEmbeddingPlan.cacheDiscriminator
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
  fontEmbeddingDiscriminator: string
): string {
  return createHash("sha256")
    .update(HYBRID_CACHE_VERSION)
    .update("\0")
    .update(presentationId)
    .update("\0")
    .update(sourceSha256)
    .update("\0")
    .update(fontEmbeddingDiscriminator)
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
