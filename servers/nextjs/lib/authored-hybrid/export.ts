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

const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;
const MAX_HYBRID_SLIDES = 100;
const MAX_FIDELITY_PPTX_BYTES = 512 * 1024 * 1024;
const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
// Total wall-clock budget for the bounded-concurrency per-slide hybrid pass. Each slide can
// take ~60s worst case (extract + up to 2 backplate relaunches); without a ceiling a
// pathological deck holds the export request for tens of minutes. When the budget is
// spent the remaining slides simply keep their fidelity (image) render.
const HYBRID_DECK_BUDGET_MS = 8 * 60_000;
const HYBRID_CACHE_VERSION = "authored-hybrid-v3-minimum-9pt-text";
const HYBRID_RESULT_CACHE_LIMIT = 24;
const HYBRID_EXPORT_CONCURRENCY = parseBoundedPositiveInt(
  process.env.AUTHORED_HYBRID_CONCURRENCY,
  4,
  1,
  8
);

const completedHybridExports = new Map<string, string>();
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
}

interface FetchedPresentation {
  presentation: StoredPresentation;
  sourceSha256: string;
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
  chromeExecutable: string
): Promise<AuthoredHybridSlideLayer | null> {
  if (!preflightAuthoredHtmlForHybrid(html).ok) return null;
  const extractionOptions = { chromeExecutable, timeoutMs: 20_000 };
  const contract = await extractAuthoredSlideDom(html, extractionOptions);
  const prepared = preSerializePreparedElements(
    await prepareNativeElements(contract.elements, {
      includeRasterText: true,
      includeRasterShapes: true,
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
  if (!selected.length) return null;

  let backplate = await renderAuthoredBackplate(
    html,
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
  if (!finalSelected.length) return null;

  if (!sameIds(finalSelected, backplate.appliedPromotedElementIds)) {
    selected = finalSelected;
    backplate = await renderAuthoredBackplate(
      html,
      contract,
      selected.map((element) => element.source.id),
      extractionOptions
    );
    if (!sameIds(selected, backplate.appliedPromotedElementIds)) return null;
  } else {
    selected = finalSelected;
  }

  return {
    slideNumber,
    backplatePng: backplate.backplatePng,
    elements: selected,
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

async function writeHybridPptx(
  fidelityPath: string,
  layers: readonly AuthoredHybridSlideLayer[]
): Promise<string> {
  const safeFidelityPath = await resolveSafeFidelityPptx(fidelityPath);
  const fidelityBytes = await fs.readFile(safeFidelityPath);
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

  try {
    const slides = Array.isArray(presentation.slides) ? presentation.slides : [];
    const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
    if (!chromeExecutable) {
      console.info(
        `[authored-hybrid] fidelity-only total=${elapsedMs(requestStartedAt)}ms ` +
          `fidelity=${fidelityMs}ms reason=chrome-unavailable`
      );
      return fidelity;
    }

    const slideInputs = slides
      .map((rawSlide, arrayIndex) => {
        if (!isRecord(rawSlide)) return null;
        const slide = rawSlide as StoredSlide;
        const html = typeof slide.html_content === "string" ? slide.html_content : "";
        return html ? { html, slideNumber: arrayIndex + 1 } : null;
      })
      .filter(
        (value): value is { html: string; slideNumber: number } => value !== null
      );

    const deckStart = Date.now();
    const slidesStartedAt = performance.now();
    let budgetWarningWritten = false;
    const preparedLayers = await mapWithConcurrency(
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
          return null;
        }
        try {
          return await prepareSlideLayer(html, slideNumber, chromeExecutable);
        } catch (error) {
          console.warn(
            `[authored-hybrid:slide-${slideNumber}] fidelity fallback after slide processing failure`,
            error
          );
          return null;
        }
      }
    );
    const layers = preparedLayers.filter(
      (layer): layer is AuthoredHybridSlideLayer => layer !== null
    );
    const slidesMs = elapsedMs(slidesStartedAt);
    if (!layers.length) return fidelity;
    const assemblyStartedAt = performance.now();
    const hybridPath = await writeHybridPptx(fidelity.path, layers);
    const assemblyMs = elapsedMs(assemblyStartedAt);
    rememberCompletedHybridExport(cacheKey, hybridPath);
    console.info(
      `[authored-hybrid] complete total=${elapsedMs(requestStartedAt)}ms ` +
        `fidelity=${fidelityMs}ms slides=${slidesMs}ms assembly=${assemblyMs}ms ` +
        `editable=${layers.length}/${slides.length} ` +
        `concurrency=${HYBRID_EXPORT_CONCURRENCY}`
    );
    return { path: hybridPath };
  } catch (error) {
    console.warn(
      "[authored-hybrid] fidelity fallback after hybrid processing failure",
      error
    );
    return fidelity;
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

  const cacheKey = createHybridCacheKey(
    params.presentationId,
    fetched.sourceSha256
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
  sourceSha256: string
): string {
  return createHash("sha256")
    .update(HYBRID_CACHE_VERSION)
    .update("\0")
    .update(presentationId)
    .update("\0")
    .update(sourceSha256)
    .digest("hex");
}

function rememberCompletedHybridExport(cacheKey: string, outPath: string): void {
  completedHybridExports.delete(cacheKey);
  completedHybridExports.set(cacheKey, outPath);
  while (completedHybridExports.size > HYBRID_RESULT_CACHE_LIMIT) {
    const oldestKey = completedHybridExports.keys().next().value;
    if (typeof oldestKey !== "string") break;
    completedHybridExports.delete(oldestKey);
  }
}

async function readCompletedHybridExport(
  cacheKey: string
): Promise<BundledPresentationExportResult | null> {
  const cachedPath = completedHybridExports.get(cacheKey);
  if (!cachedPath) return null;
  try {
    const safePath = await resolveSafeFidelityPptx(cachedPath);
    completedHybridExports.delete(cacheKey);
    completedHybridExports.set(cacheKey, safePath);
    return { path: safePath };
  } catch {
    completedHybridExports.delete(cacheKey);
    return null;
  }
}

function elapsedMs(startedAt: number): number {
  return Math.round(performance.now() - startedAt);
}
