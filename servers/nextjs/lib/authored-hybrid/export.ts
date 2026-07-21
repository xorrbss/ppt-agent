import { randomUUID } from "node:crypto";
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

const MAX_PRESENTATION_RESPONSE_BYTES = 50 * 1024 * 1024;
const MAX_HYBRID_SLIDES = 100;
const MAX_FIDELITY_PPTX_BYTES = 512 * 1024 * 1024;
const PRESENTATION_FETCH_TIMEOUT_MS = 20_000;
// Total wall-clock budget for the sequential per-slide hybrid pass. Each slide can
// take ~60s worst case (extract + up to 2 backplate relaunches); without a ceiling a
// pathological deck holds the export request for tens of minutes. When the budget is
// spent the remaining slides simply keep their fidelity (image) render.
const HYBRID_DECK_BUDGET_MS = 8 * 60_000;

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function fetchPresentation(
  presentationId: string,
  cookieHeader: string
): Promise<StoredPresentation> {
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
    return parsed as StoredPresentation;
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
    await prepareNativeElements(contract.elements)
  );
  let selected = selectLayerSafeNativeElements(contract.elements, prepared);
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
    applied
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

/**
 * Generate the historical fidelity deck first, then opportunistically replace
 * only authored slides that pass every extraction/assembly guard.
 */
export async function runAuthoredHybridPresentationExport(
  params: AuthoredHybridExportParams
): Promise<BundledPresentationExportResult> {
  const fidelity = await runBundledPresentationExport({
    format: "pptx",
    presentationId: params.presentationId,
    title: params.title,
    cookieHeader: params.cookieHeader,
  });

  try {
    const presentation = await fetchPresentation(
      params.presentationId,
      params.cookieHeader ?? ""
    );
    if (!isAuthoredPresentation(presentation) || !Array.isArray(presentation.slides)) {
      return fidelity;
    }
    if (
      presentation.slides.length === 0 ||
      presentation.slides.length > MAX_HYBRID_SLIDES
    ) {
      return fidelity;
    }
    const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
    if (!chromeExecutable) return fidelity;

    const layers: AuthoredHybridSlideLayer[] = [];
    const deckStart = Date.now();
    for (let arrayIndex = 0; arrayIndex < presentation.slides.length; arrayIndex += 1) {
      if (Date.now() - deckStart > HYBRID_DECK_BUDGET_MS) {
        console.warn(
          `[authored-hybrid] deck time budget exhausted after ${arrayIndex} slides; ` +
            "remaining slides keep the fidelity render"
        );
        break;
      }
      const rawSlide = presentation.slides[arrayIndex];
      if (!isRecord(rawSlide)) continue;
      const slide = rawSlide as StoredSlide;
      const html = typeof slide.html_content === "string" ? slide.html_content : "";
      if (!html) continue;
      const slideNumber = arrayIndex + 1;
      try {
        const layer = await prepareSlideLayer(html, slideNumber, chromeExecutable);
        if (layer) layers.push(layer);
      } catch {
        console.warn(
          `[authored-hybrid:slide-${slideNumber}] fidelity fallback after slide processing failure`
        );
      }
    }
    if (!layers.length) return fidelity;
    return { path: await writeHybridPptx(fidelity.path, layers) };
  } catch {
    console.warn("[authored-hybrid] fidelity fallback after hybrid processing failure");
    return fidelity;
  }
}
