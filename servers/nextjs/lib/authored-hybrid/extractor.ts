import {
  authoredHtmlSha256,
  assertAuthoredHybridSlide,
  buildAuthoredHybridSlide,
} from "./contract.ts";
import {
  type AuthoredHybridChromeOptions,
  runAuthoredHybridChrome,
} from "./chrome-runner.ts";
import {
  instrumentAuthoredHtml,
  normalizeAuthoredHybridBaseUrl,
  parseAuthoredHybridDomDump,
} from "./html-instrumentation.ts";
import type { AuthoredHybridExpectedPromotedElement } from "./observation.ts";
import type {
  AuthoredHybridBackplateRenderResult,
  AuthoredHybridSlideV1,
} from "./schema.ts";
import {
  AUTHORED_SLIDE_HEIGHT_PX,
  AUTHORED_SLIDE_WIDTH_PX,
} from "./schema.ts";
import sharp from "sharp";

export interface AuthoredHybridExtractionOptions
  extends AuthoredHybridChromeOptions {
  /** Base used to resolve relative HTML assets. Persist and reuse for backplate. */
  baseUrl?: string;
}

const calibratedWindowSizes = new Map<
  string,
  { width: number; height: number }
>();

async function captureWithFixedViewport(
  instrumentedHtml: string,
  screenshot: boolean,
  options: AuthoredHybridExtractionOptions
) {
  const cacheKey = options.chromeExecutable ?? `auto:${process.platform}`;
  let windowSize = calibratedWindowSizes.get(cacheKey) ?? {
    width: AUTHORED_SLIDE_WIDTH_PX,
    height: AUTHORED_SLIDE_HEIGHT_PX,
  };

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const capture = await runAuthoredHybridChrome({
      html: instrumentedHtml,
      dumpDom: true,
      screenshot,
      chromeExecutable: options.chromeExecutable,
      timeoutMs: options.timeoutMs,
      windowSizePx: windowSize,
    });
    if (!capture.serializedDom) {
      throw new Error("Chrome did not return authored hybrid DOM output.");
    }
    const observation = parseAuthoredHybridDomDump(capture.serializedDom);
    if (
      observation.viewport.widthPx === AUTHORED_SLIDE_WIDTH_PX &&
      observation.viewport.heightPx === AUTHORED_SLIDE_HEIGHT_PX
    ) {
      calibratedWindowSizes.set(cacheKey, windowSize);
      return { capture, observation };
    }
    windowSize = {
      width:
        windowSize.width +
        (AUTHORED_SLIDE_WIDTH_PX - observation.viewport.widthPx),
      height:
        windowSize.height +
        (AUTHORED_SLIDE_HEIGHT_PX - observation.viewport.heightPx),
    };
  }
  throw new Error(
    `Chrome could not calibrate the authored hybrid ${AUTHORED_SLIDE_WIDTH_PX}x${AUTHORED_SLIDE_HEIGHT_PX} viewport.`
  );
}

/** Analyse authored HTML without changing the existing fidelity export route. */
export async function extractAuthoredSlideDom(
  html: string,
  options: AuthoredHybridExtractionOptions = {}
): Promise<AuthoredHybridSlideV1> {
  const baseUrl = normalizeAuthoredHybridBaseUrl(options.baseUrl);
  const instrumented = instrumentAuthoredHtml(html, { baseUrl });
  const { observation } = await captureWithFixedViewport(
    instrumented,
    false,
    options
  );
  return buildAuthoredHybridSlide(html, observation, baseUrl);
}

function validatePromotedElementIds(
  slide: AuthoredHybridSlideV1,
  promotedElementIds: readonly string[]
): string[] {
  const promoted = [...promotedElementIds];
  const unique = new Set(promoted);
  if (unique.size !== promoted.length) {
    throw new Error("Authored hybrid promotedElementIds must not contain duplicates.");
  }
  const eligible = new Set(slide.backplate.eligibleElementIds);
  for (const id of promoted) {
    if (!eligible.has(id)) {
      throw new Error(
        `Authored hybrid element ${id} is unknown or raster-only and cannot be suppressed.`
      );
    }
  }
  return promoted;
}

function promotedElementContentKey(
  element: AuthoredHybridSlideV1["elements"][number]
): string {
  if ("text" in element && element.text !== undefined) {
    return `text:${JSON.stringify({
      role: element.text.role,
      plainText: element.text.plainText,
      paragraphs: element.text.paragraphs,
      style: element.text.style,
      runs: element.text.runs.map((run) => ({
        text: run.text,
        boundsPx: run.bounds.px,
        fragmentRectsPx: run.fragments.map((fragment) => fragment.px),
        style: run.style,
      })),
      containerShape: element.text.containerShape
        ? {
            boundsPx: element.text.containerShape.bounds.px,
            shape: element.text.containerShape.shape,
          }
        : undefined,
    })}`;
  }
  if ("image" in element) {
    return `image:${JSON.stringify({
      src: element.image.src,
      alt: element.image.alt,
      naturalWidth: element.image.naturalWidth,
      naturalHeight: element.image.naturalHeight,
      objectFit: element.image.objectFit,
      objectPosition: element.image.objectPosition,
      crop: element.image.crop,
    })}`;
  }
  if ("shape" in element) return `shape:${JSON.stringify(element.shape)}`;
  throw new Error(`Authored hybrid element ${element.id} is not native.`);
}

function expectedPromotedElements(
  slide: AuthoredHybridSlideV1,
  promotedElementIds: readonly string[]
): AuthoredHybridExpectedPromotedElement[] {
  const byId = new Map(slide.elements.map((element) => [element.id, element]));
  return promotedElementIds.map((id) => {
    const element = byId.get(id);
    const rasterText =
      element?.classification.mode === "raster" &&
      element.classification.candidateKind === "text" &&
      "text" in element &&
      element.text !== undefined;
    const rasterShape =
      element?.classification.mode === "raster" &&
      element.classification.candidateKind === "shape" &&
      "shape" in element &&
      element.shape !== undefined;
    if (!element || (element.classification.mode !== "native" && !rasterText && !rasterShape)) {
      throw new Error(`Authored hybrid element ${id} is not a promotable candidate.`);
    }
    return {
      id: element.id,
      domPath: element.domPath,
      tagName: element.tagName,
      sourceIndex: element.sourceIndex,
      candidateKind:
        element.classification.mode === "native"
          ? element.classification.kind
          : rasterText
            ? "text"
            : "shape",
      boundsPx: element.bounds.px,
      rotationDeg: element.rotationDeg,
      opacity: element.opacity,
      contentKey: promotedElementContentKey(element),
      // Suppress glyph paint only. Hiding a styled text container wholesale can
      // also hide inline SVG/icon descendants that must remain on the backplate.
      suppressWholeElement: false,
      suppressContainerPaint: Boolean(
        "text" in element && element.text?.containerShape
      ),
    };
  });
}

async function normalizeBackplatePng(png: Buffer): Promise<Buffer> {
  const metadata = await sharp(png).metadata();
  if (
    metadata.width === AUTHORED_SLIDE_WIDTH_PX &&
    metadata.height === AUTHORED_SLIDE_HEIGHT_PX
  ) {
    return png;
  }
  if (
    !metadata.width ||
    !metadata.height ||
    metadata.width < AUTHORED_SLIDE_WIDTH_PX ||
    metadata.height < AUTHORED_SLIDE_HEIGHT_PX
  ) {
    throw new Error(
      `Chrome authored hybrid backplate is ${metadata.width ?? "unknown"}x${metadata.height ?? "unknown"}; expected at least ${AUTHORED_SLIDE_WIDTH_PX}x${AUTHORED_SLIDE_HEIGHT_PX}.`
    );
  }

  // Windows headless Chrome can size the PNG to the calibrated outer window
  // even though the DOM viewport is exactly 1280x720. Page pixels begin at the
  // top-left, so crop only the excess platform chrome dimensions.
  return sharp(png)
    .extract({
      left: 0,
      top: 0,
      width: AUTHORED_SLIDE_WIDTH_PX,
      height: AUTHORED_SLIDE_HEIGHT_PX,
    })
    .png()
    .toBuffer();
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
  assertAuthoredHybridSlide(slide);
  if (authoredHtmlSha256(html) !== slide.source.htmlSha256) {
    throw new Error(
      "Authored hybrid backplate HTML does not match the analysed source fingerprint."
    );
  }
  const baseUrl = normalizeAuthoredHybridBaseUrl(options.baseUrl);
  if ((baseUrl ?? null) !== slide.source.baseUrl) {
    throw new Error(
      "Authored hybrid backplate baseUrl does not match the analysed source context."
    );
  }
  const promoted = validatePromotedElementIds(slide, promotedElementIds);
  const promotedElements = expectedPromotedElements(slide, promoted);
  const instrumented = instrumentAuthoredHtml(html, {
    baseUrl,
    promotedElements,
  });
  const { capture, observation } = await captureWithFixedViewport(
    instrumented,
    true,
    options
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
  const screenshotPng = await normalizeBackplatePng(capture.screenshotPng);
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
    fallbackElementIds: promoted.filter((id) => rejected.includes(id)),
  };
}
