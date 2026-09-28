import { createHash, randomUUID } from "node:crypto";
import {
  authoredCanvasForSize,
  LEGACY_AUTHORED_CANVAS,
  type AuthoredCanvasContract,
} from "../authored-canvas.ts";
import {
  authoredHtmlSha256,
  assertAuthoredHybridSlide,
  buildAuthoredHybridSlide,
} from "./contract.ts";
import {
  type AuthoredHybridChromeOptions,
  runTrustedSelfContainedAuthoredHybridChrome,
} from "./chrome-runner.ts";
import {
  instrumentAuthoredHtml,
  normalizeAuthoredHybridBaseUrl,
  parseAuthoredHybridDomDump,
  type AuthoredHybridFontLayoutMode,
} from "./html-instrumentation.ts";
import type { BrowserAuthoredHybridObservation } from "./observation.ts";
import type { LocalFitObservation } from "./local-fit-browser.ts";
import { rememberLocalFitSource } from "./local-fit-export-evidence.ts";
import type {
  AuthoredHybridBackplateRenderResult,
  AuthoredHybridSlideV1,
} from "./schema.ts";
import { backplatePixelSize } from "./capture-scale.ts";
import sharp from "sharp";
import { preflightTrustedSelfContainedAuthoredHtml } from "./security.ts";
import { authoredRendererIsolationPolicy } from "./renderer-isolation.ts";
import {
  expectedPromotedElements,
  validatePromotedElementIds,
} from "./promoted-elements.ts";

export interface AuthoredHybridExtractionOptions
  extends AuthoredHybridChromeOptions {
  canvas?: AuthoredCanvasContract;
  /** Base used to resolve relative HTML assets. Persist and reuse for backplate. */
  baseUrl?: string;
  /** Optional editable-layout measurement mode; omitted/default remains source fidelity. */
  fontLayoutMode?: AuthoredHybridFontLayoutMode;
  /** Exact Authored semantic element registry. Enables data-qid identity. */
  declaredElementIds?: readonly string[];
}

const calibratedWindowSizes = new Map<
  string,
  { width: number; height: number }
>();

async function captureWithFixedViewport(
  instrumentedHtml: string,
  screenshot: boolean,
  domCompletionToken: string,
  options: AuthoredHybridExtractionOptions
) {
  const canvas = options.canvas ?? LEGACY_AUTHORED_CANVAS;
  const cacheKey = `${options.chromeExecutable ?? `auto:${process.platform}`}:${canvas.widthPx}x${canvas.heightPx}`;
  let windowSize = calibratedWindowSizes.get(cacheKey) ?? {
    width: canvas.widthPx,
    height: canvas.heightPx,
  };

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const capture = await runTrustedSelfContainedAuthoredHybridChrome({
      html: instrumentedHtml,
      dumpDom: true,
      screenshot,
      chromeExecutable: options.chromeExecutable,
      timeoutMs: options.timeoutMs,
      windowSizePx: windowSize,
      domCompletionToken,
    });
    if (!capture.serializedDom) {
      throw new Error("Chrome did not return authored hybrid DOM output.");
    }
    const observation = parseAuthoredHybridDomDump(capture.serializedDom);
    if (
      observation.viewport.widthPx === canvas.widthPx &&
      observation.viewport.heightPx === canvas.heightPx
    ) {
      calibratedWindowSizes.set(cacheKey, windowSize);
      return { capture, observation };
    }
    windowSize = {
      width:
        windowSize.width +
        (canvas.widthPx - observation.viewport.widthPx),
      height:
        windowSize.height +
        (canvas.heightPx - observation.viewport.heightPx),
    };
  }
  throw new Error(
    `Chrome could not calibrate the authored hybrid ${canvas.widthPx}x${canvas.heightPx} viewport.`
  );
}

/** Analyse authored HTML without changing the existing fidelity export route. */
async function extractAuthoredSlideDomObservation(
  html: string,
  options: AuthoredHybridExtractionOptions,
  screenshot: boolean
): Promise<{
  slide: AuthoredHybridSlideV1;
  observation: BrowserAuthoredHybridObservation;
  screenshotPng?: Buffer;
}> {
  const preflight = preflightTrustedSelfContainedAuthoredHtml(html);
  if (!preflight.ok) {
    throw new Error(`Authored renderer preflight rejected HTML: ${preflight.reason ?? "unknown"}.`);
  }
  const baseUrl = normalizeAuthoredHybridBaseUrl(options.baseUrl);
  if (baseUrl && !authoredRendererIsolationPolicy().legacyFileFallbackEnabled) {
    throw new Error(
      "Authored renderer baseUrl values are disabled; assets must be embedded data URLs."
    );
  }
  const canvas = options.canvas ?? LEGACY_AUTHORED_CANVAS;
  const domCompletionToken = randomUUID();
  const instrumented = instrumentAuthoredHtml(html, {
    baseUrl,
    fontLayoutMode: options.fontLayoutMode,
    declaredElementIds: options.declaredElementIds,
    completionToken: domCompletionToken,
    canvas,
  });
  const { capture, observation } = await captureWithFixedViewport(
    instrumented,
    screenshot,
    domCompletionToken,
    { ...options, canvas }
  );
  const slide = buildAuthoredHybridSlide(html, observation, baseUrl);
  rememberLocalFitSource(slide, observation.localFitObservation);
  return {
    slide,
    observation,
    screenshotPng: capture.screenshotPng,
  };
}

/** Analyse authored HTML without changing the existing fidelity export route. */
export async function extractAuthoredSlideDom(
  html: string,
  options: AuthoredHybridExtractionOptions = {}
): Promise<AuthoredHybridSlideV1> {
  return (await extractAuthoredSlideDomObservation(html, options, false)).slide;
}

/**
 * Source-bound declared observation. It captures the exact source PNG before
 * any Hybrid suppression and returns readiness facts for the server receipt.
 */
export async function extractDeclaredAuthoredSlideDom(
  html: string,
  declaredElementIds: readonly string[],
  options: AuthoredHybridExtractionOptions = {}
): Promise<{
  slide: AuthoredHybridSlideV1;
  observation: BrowserAuthoredHybridObservation;
  capturedPng: Buffer;
  capturedPngSha256: string;
}> {
  if (!declaredElementIds.length) {
    throw new Error("authored_semantic_qid_mismatch");
  }
  const result = await extractAuthoredSlideDomObservation(
    html,
    { ...options, declaredElementIds },
    true
  );
  if (!result.screenshotPng) {
    throw new Error("authored_browser_observation_png_missing");
  }
  const capturedPng = await normalizeBackplatePng(
    result.screenshotPng,
    options.canvas ?? LEGACY_AUTHORED_CANVAS,
  );
  return {
    slide: result.slide,
    observation: result.observation,
    capturedPng,
    capturedPngSha256: `sha256:${createHash("sha256").update(capturedPng).digest("hex")}`,
  };
}

async function normalizeBackplatePng(
  png: Buffer,
  canvas: AuthoredCanvasContract,
): Promise<Buffer> {
  const metadata = await sharp(png).metadata();
  const expected = backplatePixelSize(canvas);
  if (
    metadata.width === expected.width &&
    metadata.height === expected.height
  ) {
    return png;
  }
  if (
    !metadata.width ||
    !metadata.height ||
    metadata.width < expected.width ||
    metadata.height < expected.height
  ) {
    throw new Error(
      `Chrome authored hybrid backplate is ${metadata.width ?? "unknown"}x${metadata.height ?? "unknown"}; expected at least ${expected.width}x${expected.height}.`
    );
  }

  // Windows headless Chrome can size the PNG to the calibrated outer window
  // even though the DOM viewport is exactly 1280x720 CSS px; crop only the
  // excess platform chrome (already at the configured device-pixel scale).
  return sharp(png)
    .extract({
      left: 0,
      top: 0,
      width: expected.width,
      height: expected.height,
    })
    .png()
    .toBuffer();
}

/**
 * Exact Fidelity capture. The browser analyser takes a dedicated early path:
 * no font substitution, minimum-size rewrite, direct-text wrapper, QID marker,
 * or native-paint suppression runs before Page.captureScreenshot.
 */
export async function renderAuthoredFidelityBackplate(
  html: string,
  options: AuthoredHybridExtractionOptions = {}
): Promise<AuthoredHybridBackplateRenderResult> {
  const preflight = preflightTrustedSelfContainedAuthoredHtml(html);
  if (!preflight.ok) {
    throw new Error(`Authored renderer preflight rejected HTML: ${preflight.reason ?? "unknown"}.`);
  }
  const baseUrl = normalizeAuthoredHybridBaseUrl(options.baseUrl);
  if (baseUrl && !authoredRendererIsolationPolicy().legacyFileFallbackEnabled) {
    throw new Error(
      "Authored renderer baseUrl values are disabled; assets must be embedded data URLs."
    );
  }
  const canvas = options.canvas ?? LEGACY_AUTHORED_CANVAS;
  const domCompletionToken = randomUUID();
  const instrumented = instrumentAuthoredHtml(html, {
    baseUrl,
    captureMode: "fidelity",
    completionToken: domCompletionToken,
    canvas,
  });
  const { capture, observation } = await captureWithFixedViewport(
    instrumented,
    true,
    domCompletionToken,
    { ...options, canvas }
  );
  if (!capture.screenshotPng) {
    throw new Error("Chrome did not return the complete authored Fidelity capture.");
  }
  const screenshotPng = await normalizeBackplatePng(
    capture.screenshotPng,
    canvas,
  );
  return {
    backplatePng: screenshotPng,
    appliedPromotedElementIds: [],
    localFitObservation: observation.localFitObservation,
    fallbackElementIds: [],
    capturePngSha256: `sha256:${createHash("sha256").update(screenshotPng).digest("hex")}`,
  };
}

/**
 * Capture the fidelity backplate while suppressing only H2 elements that were
 * assembled successfully. Omitting a candidate ID is the per-element raster
 * fallback and leaves it visible in the PNG.
 */
export async function renderAuthoredBackplate(
  html: string,
  slide: AuthoredHybridSlideV1,
  promotedElementIds: readonly string[],
  options: AuthoredHybridExtractionOptions = {}
): Promise<AuthoredHybridBackplateRenderResult> {
  const preflight = preflightTrustedSelfContainedAuthoredHtml(html);
  if (!preflight.ok) {
    throw new Error(`Authored renderer preflight rejected HTML: ${preflight.reason ?? "unknown"}.`);
  }
  assertAuthoredHybridSlide(slide);
  const canvas = options.canvas ?? authoredCanvasForSize(
    slide.source.viewport.widthPx,
    slide.source.viewport.heightPx,
  );
  if (authoredHtmlSha256(html) !== slide.source.htmlSha256) {
    throw new Error(
      "Authored hybrid backplate HTML does not match the analysed source fingerprint."
    );
  }
  const baseUrl = normalizeAuthoredHybridBaseUrl(options.baseUrl);
  if (baseUrl && !authoredRendererIsolationPolicy().legacyFileFallbackEnabled) {
    throw new Error(
      "Authored renderer baseUrl values are disabled; assets must be embedded data URLs."
    );
  }
  if ((baseUrl ?? null) !== slide.source.baseUrl) {
    throw new Error(
      "Authored hybrid backplate baseUrl does not match the analysed source context."
    );
  }
  const promoted = validatePromotedElementIds(slide, promotedElementIds);
  const promotedElements = expectedPromotedElements(slide, promoted);
  const domCompletionToken = randomUUID();
  const instrumented = instrumentAuthoredHtml(html, {
    baseUrl,
    promotedElements,
    captureMode: "hybrid-backplate",
    declaredElementIds: slide.elements.every(
      (element) => element.identityAuthority === "declared-qid"
    )
      ? slide.elements.map((element) => element.id)
      : undefined,
    completionToken: domCompletionToken,
    canvas,
  });
  const { capture, observation } = await captureWithFixedViewport(
    instrumented,
    true,
    domCompletionToken,
    { ...options, canvas }
  );
  if (!capture.screenshotPng) {
    throw new Error("Chrome did not return the complete authored hybrid backplate capture.");
  }
  const applied = observation.appliedPromotedElementIds;
  const rejected = observation.rejectedPromotedElementIds;
  const observed = [...applied, ...rejected];
  if (
    new Set(observed).size !== observed.length ||
    observed.length !== promoted.length ||
    promoted.some((id) => !observed.includes(id)) ||
    observed.some((id) => !promoted.includes(id))
  ) {
    throw new Error(
      "Authored hybrid backplate did not account for every requested element ID."
    );
  }
  const screenshotPng = await normalizeBackplatePng(capture.screenshotPng, canvas);
  if (
    screenshotPng.length < 24 ||
    !screenshotPng.subarray(0, 8).equals(
      Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
    )
  ) {
    throw new Error("Authored hybrid backplate output is not a valid PNG stream.");
  }
  return {
    backplatePng: screenshotPng,
    appliedPromotedElementIds: promoted.filter((id) => applied.includes(id)),
    localFitObservation: observation.localFitObservation,
    fallbackElementIds: promoted.filter((id) => rejected.includes(id)),
    capturePngSha256: `sha256:${createHash("sha256").update(screenshotPng).digest("hex")}`,
  };
}

export interface AuthoredHybridRasterPaintBandRenderResult {
  readonly localFitObservation?: LocalFitObservation;
  readonly rasterPng: Buffer;
  readonly elementIds: readonly string[];
  readonly capturePngSha256: string;
}

/** Capture one transparent Chromium paint band without changing layout. */
export async function renderAuthoredRasterPaintBand(
  html: string,
  slide: AuthoredHybridSlideV1,
  elementIds: readonly string[],
  options: AuthoredHybridExtractionOptions = {},
): Promise<AuthoredHybridRasterPaintBandRenderResult> {
  const preflight = preflightTrustedSelfContainedAuthoredHtml(html);
  if (!preflight.ok) {
    throw new Error(`Authored renderer preflight rejected HTML: ${preflight.reason ?? "unknown"}.`);
  }
  assertAuthoredHybridSlide(slide);
  const canvas = options.canvas ?? authoredCanvasForSize(
    slide.source.viewport.widthPx,
    slide.source.viewport.heightPx,
  );
  if (authoredHtmlSha256(html) !== slide.source.htmlSha256) {
    throw new Error("Authored hybrid paint band source fingerprint mismatch.");
  }
  if (!slide.elements.every((element) => element.identityAuthority === "declared-qid")) {
    throw new Error("Authored hybrid paint bands require declared QID identity.");
  }
  const requested = [...elementIds];
  const known = new Set(slide.elements.map((element) => element.id));
  if (
    new Set(requested).size !== requested.length ||
    requested.some((id) => !known.has(id))
  ) {
    throw new Error("Authored hybrid paint band QIDs must be unique declared owners.");
  }
  const baseUrl = normalizeAuthoredHybridBaseUrl(options.baseUrl);
  if ((baseUrl ?? null) !== slide.source.baseUrl) {
    throw new Error("Authored hybrid paint band baseUrl mismatch.");
  }
  const domCompletionToken = randomUUID();
  const instrumented = instrumentAuthoredHtml(html, {
    baseUrl,
    captureMode: "hybrid-paint-band",
    paintBandElementIds: requested,
    declaredElementIds: slide.elements.map((element) => element.id),
    completionToken: domCompletionToken,
    canvas,
  });
  const { capture, observation } = await captureWithFixedViewport(
    instrumented,
    true,
    domCompletionToken,
    { ...options, canvas },
  );
  if (!capture.screenshotPng) {
    throw new Error("Chrome did not return the authored hybrid paint band.");
  }
  if (
    JSON.stringify(observation.appliedPaintBandElementIds ?? []) !==
      JSON.stringify(requested) ||
    JSON.stringify(buildAuthoredHybridSlide(html, observation, baseUrl).elements) !==
      JSON.stringify(slide.elements)
  ) {
    throw new Error("Authored hybrid paint band observation drifted from its source contract.");
  }
  const rasterPng = await normalizeBackplatePng(capture.screenshotPng, canvas);
  return {
    rasterPng,
    localFitObservation: observation.localFitObservation,
    elementIds: requested,
    capturePngSha256: `sha256:${createHash("sha256").update(rasterPng).digest("hex")}`,
  };
}
