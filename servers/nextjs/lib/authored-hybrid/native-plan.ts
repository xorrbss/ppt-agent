import sharp from "sharp";

import type {
  AuthoredHybridBounds,
  AuthoredHybridColor,
  AuthoredHybridElement,
  AuthoredHybridNativeImageElement,
  AuthoredHybridNativeShapeElement,
  AuthoredHybridNativeTextElement,
  AuthoredHybridRasterElement,
  AuthoredHybridRect,
  AuthoredHybridShapePayload,
  AuthoredHybridTextPayload,
  AuthoredHybridTextStyle,
} from "./schema.ts";
import { validateHybridDataImageUrl } from "./security.ts";

export const EMU_PER_CSS_PX = 9_525;
export const MIN_EDITABLE_FONT_SIZE_PT = 9;
const EMU_PER_POINT = 12_700;
const MAX_NATIVE_ELEMENTS_PER_SLIDE = 1_000;
const MAX_IMAGE_PIXELS = 40_000_000;
const MAX_TEXT_CODE_POINTS = 100_000;

export type PreparedNativeElement =
  | { kind: "text"; source: AuthoredHybridEditableTextElement }
  | { kind: "shape"; source: AuthoredHybridEditableShapeElement }
  | {
      kind: "image";
      source: AuthoredHybridNativeImageElement;
      png: Buffer;
    };

type AuthoredHybridNativeElement =
  | AuthoredHybridNativeTextElement
  | AuthoredHybridNativeImageElement
  | AuthoredHybridNativeShapeElement;

type AuthoredHybridEditableTextElement =
  | AuthoredHybridNativeTextElement
  | (AuthoredHybridRasterElement & { text: AuthoredHybridTextPayload });

type AuthoredHybridEditableShapeElement =
  | AuthoredHybridNativeShapeElement
  | (AuthoredHybridRasterElement & { shape: AuthoredHybridShapePayload });

type AuthoredHybridPromotableElement =
  | AuthoredHybridNativeElement
  | AuthoredHybridEditableTextElement
  | AuthoredHybridEditableShapeElement;

export interface PrepareNativeElementOptions {
  includeRasterText?: boolean;
  includeRasterShapes?: boolean;
}

export interface LayerSelectionOptions {
  promoteTextAboveRaster?: boolean;
  promoteShapesAboveRaster?: boolean;
}

function isEditableRasterText(
  element: AuthoredHybridElement
): element is AuthoredHybridRasterElement & { text: AuthoredHybridTextPayload } {
  return (
    element.classification.mode === "raster" &&
    element.classification.candidateKind === "text" &&
    "text" in element &&
    element.text !== undefined
  );
}

function isEditableTextCandidate(
  element: AuthoredHybridPromotableElement
): element is AuthoredHybridEditableTextElement {
  return element.classification.mode === "native"
    ? element.classification.kind === "text"
    : isEditableRasterText(element);
}

const UNSAFE_RASTER_SHAPE_REASONS = new Set([
  "extraction-error",
  "invalid-bounds",
  "outside-slide",
  "clip-path",
  "mask",
  "filter",
  "backdrop-filter",
  "mix-blend-mode",
  "complex-transform",
  "transformed-ancestor",
  "animated",
  "unsupported-background",
  "unsupported-opacity",
]);

function isEditableRasterShape(
  element: AuthoredHybridElement
): element is AuthoredHybridRasterElement & { shape: AuthoredHybridShapePayload } {
  return (
    element.classification.mode === "raster" &&
    element.classification.candidateKind === "shape" &&
    "shape" in element &&
    element.shape !== undefined &&
    !element.classification.reasons.some((reason) =>
      UNSAFE_RASTER_SHAPE_REASONS.has(reason)
    ) &&
    Boolean(
      element.shape.fill ||
        element.shape.gradient ||
        element.shape.stroke ||
        element.shape.borderLines?.length ||
        element.shape.shadowLayers?.length
    )
  );
}

function isEditableShapeCandidate(
  element: AuthoredHybridPromotableElement
): element is AuthoredHybridEditableShapeElement {
  return element.classification.mode === "native"
    ? element.classification.kind === "shape"
    : isEditableRasterShape(element);
}

function xmlTextIsSafe(value: string): boolean {
  if ([...value].length > MAX_TEXT_CODE_POINTS) return false;
  for (const character of value) {
    const point = character.codePointAt(0) ?? 0;
    if (
      point !== 0x09 &&
      point !== 0x0a &&
      point !== 0x0d &&
      !(point >= 0x20 && point <= 0xd7ff) &&
      !(point >= 0xe000 && point <= 0xfffd) &&
      !(point >= 0x10000 && point <= 0x10ffff)
    ) {
      return false;
    }
  }
  return true;
}

function boundsAreSafe(element: AuthoredHybridElement): boolean {
  const { x, y, width, height } = element.bounds.px;
  const geometryIsValid =
    [x, y, width, height, element.rotationDeg, element.opacity].every(Number.isFinite) &&
    width > 0 &&
    height > 0 &&
    element.opacity >= 0 &&
    element.opacity <= 1;
  if (!geometryIsValid) return false;

  if (isEditableTextCandidate(element as AuthoredHybridPromotableElement)) {
    // Edge-aligned footers and rotated labels can have an unrotated CSS text
    // box that crosses the slide boundary even though the visible glyphs are
    // intentionally inside it. PowerPoint clips slide content at the canvas,
    // so retain any text box that still intersects the slide instead of
    // silently dropping its complete text payload.
    return x < 1280.01 && y < 720.01 && x + width > -0.01 && y + height > -0.01;
  }

  return (
    x >= -0.01 &&
    y >= -0.01 &&
    x + width <= 1280.01 &&
    y + height <= 720.01
  );
}

function clipUnrotatedTextToSlide<T extends AuthoredHybridEditableTextElement>(
  element: T
): T {
  if (Math.abs(element.rotationDeg) >= 0.001) return element;
  const clip = (bounds: AuthoredHybridBounds): AuthoredHybridBounds => {
    const left = Math.max(0, bounds.px.x);
    const top = Math.max(0, bounds.px.y);
    const right = Math.min(1280, bounds.px.x + bounds.px.width);
    const bottom = Math.min(720, bounds.px.y + bounds.px.height);
    const px = {
      x: left,
      y: top,
      width: Math.max(0.01, right - left),
      height: Math.max(0.01, bottom - top),
    };
    return {
      px,
      inches: {
        x: px.x / 96,
        y: px.y / 96,
        width: px.width / 96,
        height: px.height / 96,
      },
    };
  };
  const bounds = clip(element.bounds);
  const text = element.text.containerShape
    ? {
        ...element.text,
        containerShape: {
          ...element.text.containerShape,
          bounds: clip(element.text.containerShape.bounds),
        },
      }
    : element.text;
  return { ...element, bounds, text } as T;
}

function styleIsSafe(style: AuthoredHybridTextStyle): boolean {
  return (
    [
      style.fontSizePt,
      style.lineHeight.points,
      style.letterSpacingPt,
      style.color.alpha,
    ].every(Number.isFinite) &&
    style.fontSizePt >= 1 &&
    style.fontSizePt <= 400 &&
    style.lineHeight.points >= 0 &&
    style.letterSpacingPt >= -100 &&
    style.letterSpacingPt <= 100 &&
    style.fontFamilies.every(xmlTextIsSafe) &&
    style.cjkFallbackFamilies.every(xmlTextIsSafe) &&
    xmlTextIsSafe(style.fontFamily)
  );
}

async function renderImageToBox(
  element: AuthoredHybridNativeImageElement
): Promise<Buffer | null> {
  const decoded = validateHybridDataImageUrl(element.image.src);
  if (!decoded.ok) return null;
  const width = Math.max(1, Math.round(element.bounds.px.width));
  const height = Math.max(1, Math.round(element.bounds.px.height));
  const input = sharp(decoded.bytes, {
    failOn: "error",
    limitInputPixels: MAX_IMAGE_PIXELS,
    sequentialRead: true,
  }).rotate();
  const metadata = await input.metadata();
  if (
    !metadata.width ||
    !metadata.height ||
    metadata.width * metadata.height > MAX_IMAGE_PIXELS
  ) {
    return null;
  }

  const transparent = { r: 0, g: 0, b: 0, alpha: 0 };
  let pipeline: sharp.Sharp;
  if (element.image.objectFit === "fill") {
    pipeline = input.resize(width, height, { fit: "fill" });
  } else if (element.image.objectFit === "cover") {
    pipeline = input.resize(width, height, {
      fit: "cover",
      position: "centre",
    });
  } else if (element.image.objectFit === "contain") {
    pipeline = input.resize(width, height, {
      fit: "contain",
      position: "centre",
      background: transparent,
    });
  } else if (element.image.objectFit === "scale-down") {
    pipeline = input.resize(width, height, {
      fit: "contain",
      position: "centre",
      withoutEnlargement: true,
      background: transparent,
    });
  } else {
    // CSS object-fit:none keeps intrinsic pixels and clips centrally. A larger
    // source needs explicit extraction; smaller sources can use contain without
    // enlargement, which centres the original pixels on a transparent canvas.
    if (metadata.width <= width && metadata.height <= height) {
      pipeline = input.resize(width, height, {
        fit: "contain",
        position: "centre",
        withoutEnlargement: true,
        background: transparent,
      });
    } else {
      const left = Math.max(0, Math.floor((metadata.width - width) / 2));
      const top = Math.max(0, Math.floor((metadata.height - height) / 2));
      const cropWidth = Math.min(width, metadata.width - left);
      const cropHeight = Math.min(height, metadata.height - top);
      const cropped = await input
        .extract({ left, top, width: cropWidth, height: cropHeight })
        .png()
        .toBuffer();
      pipeline = sharp({
        create: { width, height, channels: 4, background: transparent },
      }).composite([
        {
          input: cropped,
          left: Math.max(0, Math.floor((width - cropWidth) / 2)),
          top: Math.max(0, Math.floor((height - cropHeight) / 2)),
        },
      ]);
    }
  }
  const png = await pipeline.png({ compressionLevel: 6 }).toBuffer();
  return png.length <= 32 * 1024 * 1024 ? png : null;
}

export async function prepareNativeElements(
  elements: readonly AuthoredHybridElement[],
  options: PrepareNativeElementOptions = {}
): Promise<PreparedNativeElement[]> {
  const candidates = elements
    .filter(
      (element): element is AuthoredHybridPromotableElement =>
        element.classification.mode === "native" ||
        (options.includeRasterText === true && isEditableRasterText(element)) ||
        (options.includeRasterShapes === true && isEditableRasterShape(element))
    )
    .slice(0, MAX_NATIVE_ELEMENTS_PER_SLIDE);
  const prepared: PreparedNativeElement[] = [];
  for (const candidate of candidates) {
    if (!boundsAreSafe(candidate)) continue;
    try {
      if (isEditableTextCandidate(candidate)) {
        if (
          !xmlTextIsSafe(candidate.text.plainText) ||
          !styleIsSafe(candidate.text.style) ||
          candidate.text.runs.some(
            (run) => !xmlTextIsSafe(run.text) || !styleIsSafe(run.style)
          )
        ) {
          continue;
        }
        prepared.push({
          kind: "text",
          source: clipUnrotatedTextToSlide(enforceMinimumTextSize(candidate)),
        });
      } else if (isEditableShapeCandidate(candidate)) {
        prepared.push({ kind: "shape", source: candidate });
      } else {
        const png = await renderImageToBox(candidate);
        if (png) prepared.push({ kind: "image", source: candidate, png });
      }
    } catch {
      // Element-level raster fallback: no source data or URLs are logged.
    }
  }
  return prepared;
}

function intersects(a: AuthoredHybridElement, b: AuthoredHybridElement): boolean {
  const ar = a.bounds.px;
  const br = b.bounds.px;
  return (
    ar.x < br.x + br.width &&
    ar.x + ar.width > br.x &&
    ar.y < br.y + br.height &&
    ar.y + ar.height > br.y
  );
}

/**
 * A single backplate is below every native object. Do not promote an object if
 * a higher raster object overlaps it, because that would invert their z-order.
 */
export function selectLayerSafeNativeElements(
  allElements: readonly AuthoredHybridElement[],
  prepared: readonly PreparedNativeElement[],
  allowedIds: ReadonlySet<string> = new Set(prepared.map((item) => item.source.id)),
  options: LayerSelectionOptions = {}
): PreparedNativeElement[] {
  const eligible = prepared.filter((item) => allowedIds.has(item.source.id));
  const promotedIds = new Set(eligible.map((item) => item.source.id));
  const isAbove = (other: AuthoredHybridElement, item: PreparedNativeElement) =>
    other.zOrder > item.source.zOrder ||
    (other.zOrder === item.source.zOrder &&
      other.sourceIndex > item.source.sourceIndex);

  // Removing one native candidate puts it back on the backplate. That newly
  // rasterised element can in turn occlude a lower candidate, so converge to a
  // fixed point instead of making a single pass over the initial raster set.
  let changed = true;
  while (changed) {
    changed = false;
    for (const item of eligible) {
      if (!promotedIds.has(item.source.id)) continue;
      if (options.promoteTextAboveRaster === true && item.kind === "text") continue;
      if (options.promoteShapesAboveRaster === true && item.kind === "shape") continue;
      // A semantic HTML illustration is intentionally captured as one picture.
      // Its child nodes still appear in the extraction list, so treating those
      // children as raster occluders would incorrectly discard the picture.
      if (isCompositeHtmlIllustration(item)) continue;
      if (
        allElements.some(
          (other) =>
            !promotedIds.has(other.id) &&
            isAbove(other, item) &&
            intersects(item.source, other)
        )
      ) {
        promotedIds.delete(item.source.id);
        changed = true;
      }
    }
  }

  return eligible
    .filter((item) => promotedIds.has(item.source.id))
    .sort(
      (left, right) =>
        left.source.zOrder - right.source.zOrder ||
        left.source.sourceIndex - right.source.sourceIndex
    );
}

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function emu(value: number): number {
  return Math.max(0, Math.round(value * EMU_PER_CSS_PX));
}

function rotation(value: number): number {
  const normalized = ((value % 360) + 360) % 360;
  return Math.round(normalized * 60_000);
}

function transformRectXml(rect: AuthoredHybridRect, rotationDeg: number): string {
  return `<a:xfrm${rotation(rotationDeg) ? ` rot="${rotation(rotationDeg)}"` : ""}><a:off x="${emu(rect.x)}" y="${emu(rect.y)}"/><a:ext cx="${emu(rect.width)}" cy="${emu(rect.height)}"/></a:xfrm>`;
}

function isCompositeHtmlIllustration(item: PreparedNativeElement): boolean {
  if (item.kind !== "image") return false;
  const tagName = item.source.tagName.trim().toLowerCase();
  return tagName !== "img" && tagName !== "svg" && item.source.image.alt.trim().length > 0;
}

function transformXml(element: AuthoredHybridElement): string {
  return transformRectXml(element.bounds.px, element.rotationDeg);
}

function alphaAmount(color: AuthoredHybridColor, opacity: number): number {
  return Math.max(0, Math.min(100_000, Math.round(color.alpha * opacity * 100_000)));
}

function colorXml(color: AuthoredHybridColor, opacity: number): string {
  const alpha = alphaAmount(color, opacity);
  return `<a:solidFill><a:srgbClr val="${color.hex}">${alpha < 100_000 ? `<a:alpha val="${alpha}"/>` : ""}</a:srgbClr></a:solidFill>`;
}

function gradientColorXml(color: AuthoredHybridColor, opacity: number): string {
  const alpha = alphaAmount(color, opacity);
  return `<a:srgbClr val="${color.hex}">${alpha < 100_000 ? `<a:alpha val="${alpha}"/>` : ""}</a:srgbClr>`;
}

function fontName(candidates: readonly string[], fallback: string): string {
  const generic = new Set([
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
  ]);
  const selected = candidates.find(
    (candidate) => candidate.trim() && !generic.has(candidate.trim().toLowerCase())
  );
  return (selected ?? fallback).trim().slice(0, 127);
}

function runPropertiesXml(
  style: AuthoredHybridTextStyle,
  opacity: number
): string {
  const latin = fontName(style.fontFamilies, "Aptos");
  const eastAsian = fontName(
    style.cjkFallbackFamilies,
    process.platform === "win32" ? "Malgun Gothic" : "Noto Sans CJK KR"
  );
  const attributes = [
    `lang="ko-KR"`,
    `altLang="en-US"`,
    `sz="${Math.round(Math.max(MIN_EDITABLE_FONT_SIZE_PT, style.fontSizePt) * 100)}"`,
    style.bold ? `b="1"` : "",
    style.italic ? `i="1"` : "",
    style.underline ? `u="sng"` : "",
    style.strike ? `strike="sngStrike"` : "",
    style.letterSpacingPt
      ? `spc="${Math.round(style.letterSpacingPt * 100)}"`
      : "",
  ].filter(Boolean);
  return `<a:rPr ${attributes.join(" ")}>${colorXml(style.color, opacity)}<a:latin typeface="${escapeXml(latin)}"/><a:ea typeface="${escapeXml(eastAsian)}"/><a:cs typeface="${escapeXml(latin)}"/></a:rPr>`;
}

interface TextSegment {
  text: string;
  style: AuthoredHybridTextStyle;
}

function collapseCapturedLineBreaks(element: AuthoredHybridEditableTextElement): boolean {
  if (!element.text.plainText.includes("\n")) return false;
  const lineHeightPx = Math.max(
    element.text.style.fontSizePt / 0.75,
    element.text.style.lineHeight.points / 0.75
  );
  const availableHeight = Math.max(
    element.bounds.px.height,
    element.text.containerShape?.bounds.px.height ?? 0
  );
  if (availableHeight > lineHeightPx * 1.5 + 1) return false;

  const rects = element.text.runs
    .flatMap((run) =>
      run.fragments.length
        ? run.fragments.map((fragment) => fragment.px)
        : [run.bounds.px]
    )
    .filter((rect) => rect.width > 0 && rect.height > 0);
  if (!rects.length) return true;
  const first = rects[0];
  return rects.every((rect) => {
    const overlap = Math.min(first.y + first.height, rect.y + rect.height) -
      Math.max(first.y, rect.y);
    return overlap >= Math.min(first.height, rect.height) * 0.25;
  });
}

function textSegments(element: AuthoredHybridEditableTextElement): TextSegment[] {
  const collapseBreaks = collapseCapturedLineBreaks(element);
  const joined = element.text.runs.map((run) => run.text).join("");
  const sameTextIgnoringCollapsibleWhitespace =
    joined.replace(/\s+/g, " ").trim() ===
    element.text.plainText.replace(/\s+/g, " ").trim();
  if (
    element.text.runs.length &&
    (joined === element.text.plainText || sameTextIgnoringCollapsibleWhitespace)
  ) {
    return element.text.runs.map((run) => ({
      text: collapseBreaks ? run.text.replace(/\n+/g, " ") : run.text,
      style: run.style,
    }));
  }
  return [{
    text: collapseBreaks ? element.text.plainText.replace(/\n+/g, " ") : element.text.plainText,
    style: element.text.style,
  }];
}

function paragraphsXml(element: AuthoredHybridEditableTextElement): string {
  const alignment = {
    left: "l",
    center: "ctr",
    right: "r",
    justify: "just",
  }[element.text.style.horizontalAlignment];
  const lineSpacing = Math.max(
    1_000,
    Math.min(500_000, Math.round(element.text.style.lineHeight.multiple * 100_000))
  );
  const pPr = `<a:pPr algn="${alignment}" marL="0" indent="0"${element.text.style.direction === "rtl" ? ` rtl="1"` : ""}><a:lnSpc><a:spcPct val="${lineSpacing}"/></a:lnSpc><a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft></a:pPr>`;
  const contents: string[] = [];
  for (const segment of textSegments(element)) {
    const pieces = segment.text.split("\n");
    pieces.forEach((piece, index) => {
      // Browser visual lines and HTML <br> nodes are line breaks inside one
      // text block, not separate PowerPoint paragraphs. Separate paragraphs
      // can inherit theme spacing and clip the final line in tight Korean UI
      // labels even when every glyph is present in the OOXML.
      if (index > 0) contents.push("<a:br/>");
      if (piece || pieces.length === 1) {
        contents.push(
          `<a:r>${runPropertiesXml(segment.style, element.opacity)}<a:t xml:space="preserve">${escapeXml(piece)}</a:t></a:r>`
        );
      }
    });
  }
  return `<a:p>${pPr}${contents.join("")}<a:endParaRPr lang="ko-KR"/></a:p>`;
}

function powerPointSafeTextInsets(
  element: AuthoredHybridEditableTextElement,
  shapeBounds: AuthoredHybridRect,
  insets: { left: number; top: number; right: number; bottom: number }
): { left: number; top: number; right: number; bottom: number } {
  const lineCount = collapseCapturedLineBreaks(element)
    ? 1
    : Math.max(
        1,
        textSegments(element).reduce(
          (count, segment) => count + segment.text.split("\n").length - 1,
          1
        )
      );
  const lineHeightPx = Math.max(
    element.text.style.fontSizePt / 0.75,
    element.text.style.lineHeight.points / 0.75
  );
  // Range rectangles describe painted glyphs and are often shorter than the
  // line boxes PowerPoint allocates (notably for Malgun Gothic). Reclaim only
  // the container padding needed to hold the captured lines, plus a small
  // per-line metric tolerance, while keeping the text inside the authored
  // outer box. This prevents PowerPoint from hiding a complete final line in
  // tight two-line cards even though the OOXML still contains every run.
  const requiredHeight = Math.min(
    shapeBounds.height,
    lineCount * lineHeightPx + Math.max(2, lineCount * 2)
  );
  const availableHeight = Math.max(0, shapeBounds.height - insets.top - insets.bottom);
  let reclaim = Math.max(0, requiredHeight - availableHeight);
  if (reclaim <= 0) return insets;

  let top = insets.top;
  let bottom = insets.bottom;
  const alignment = element.text.style.verticalAlignment;
  if (alignment === "top") {
    const fromBottom = Math.min(bottom, reclaim);
    bottom -= fromBottom;
    reclaim -= fromBottom;
    top = Math.max(0, top - reclaim);
  } else if (alignment === "bottom") {
    const fromTop = Math.min(top, reclaim);
    top -= fromTop;
    reclaim -= fromTop;
    bottom = Math.max(0, bottom - reclaim);
  } else {
    const fromTop = Math.min(top, reclaim / 2);
    top -= fromTop;
    reclaim -= fromTop;
    const fromBottom = Math.min(bottom, reclaim);
    bottom -= fromBottom;
    reclaim -= fromBottom;
    top = Math.max(0, top - reclaim);
  }
  return { ...insets, top, bottom };
}

function powerPointSafeTextBounds(
  element: AuthoredHybridEditableTextElement,
  bounds: AuthoredHybridRect
): AuthoredHybridRect {
  if (Math.abs(element.rotationDeg) >= 0.001) return bounds;
  const segments = textSegments(element);
  const lineHeightsPx = [0];
  for (const segment of segments) {
    const pieces = segment.text.split("\n");
    const runLineHeightPx = Math.max(
      (segment.style.fontSizePt * segment.style.lineHeight.multiple) / 0.75,
      segment.style.lineHeight.points / 0.75
    );
    pieces.forEach((piece, index) => {
      if (index > 0) lineHeightsPx.push(0);
      if (piece || pieces.length === 1) {
        const lineIndex = lineHeightsPx.length - 1;
        lineHeightsPx[lineIndex] = Math.max(
          lineHeightsPx[lineIndex],
          runLineHeightPx
        );
      }
    });
  }
  const fallbackLineHeightPx = Math.max(
    (element.text.style.fontSizePt * element.text.style.lineHeight.multiple) / 0.75,
    element.text.style.lineHeight.points / 0.75
  );
  const lineCount = lineHeightsPx.length;
  // Chromium can fit CJK glyphs into a line box whose height is exactly the
  // computed CSS line-height. PowerPoint reserves a little more ascent and
  // descent for its substituted East Asian font. Mixed-size runs make this
  // especially visible: the large first line consumes more height than the
  // root style predicts and PowerPoint can clip the complete final line. Sum
  // the largest run height on each line and add six pixels of metric tolerance
  // per line. Grow only standalone text boxes; container text must stay within
  // its authored card.
  const requiredHeight = Math.min(
    720,
    lineHeightsPx.reduce(
      (total, height) => total + Math.max(height, fallbackLineHeightPx),
      0
    ) + Math.max(4, lineCount * 6)
  );
  if (bounds.height >= requiredHeight - 0.01) return bounds;

  const extra = requiredHeight - bounds.height;
  let y = bounds.y;
  if (element.text.style.verticalAlignment === "bottom") y -= extra;
  else if (element.text.style.verticalAlignment === "middle") y -= extra / 2;

  // Keep the enlarged editable box on the slide. This prevents the safety
  // allowance itself from creating off-canvas text at footers and edge labels.
  y = Math.max(0, Math.min(720 - requiredHeight, y));
  return {
    x: bounds.x,
    y,
    width: bounds.width,
    height: requiredHeight,
  };
}

function shapePreset(shape: AuthoredHybridShapePayload): string {
  return shape.shape === "round-rectangle"
    ? "roundRect"
    : shape.shape === "ellipse"
      ? "ellipse"
      : shape.shape === "line"
        ? "line"
        : "rect";
}

function shapeGeometryXml(
  shape: AuthoredHybridShapePayload,
  bounds: AuthoredHybridRect
): string {
  if (shape.shape === "freeform") {
    const points = shape.points ?? [];
    const pathWidth = 100_000;
    const pathHeight = 100_000;
    const vertexXml = points
      .map((point, index) => {
        const x = Math.max(0, Math.min(pathWidth, Math.round(point.x * pathWidth)));
        const y = Math.max(0, Math.min(pathHeight, Math.round(point.y * pathHeight)));
        const tag = index === 0 ? "a:moveTo" : "a:lnTo";
        return `<${tag}><a:pt x="${x}" y="${y}"/></${tag}>`;
      })
      .join("");
    return `<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/><a:rect l="l" t="t" r="r" b="b"/><a:pathLst><a:path w="${pathWidth}" h="${pathHeight}">${vertexXml}${shape.closed ? "<a:close/>" : ""}</a:path></a:pathLst></a:custGeom>`;
  }
  const preset = shapePreset(shape);
  const radiusAdjustment =
    preset === "roundRect"
      ? `<a:gd name="adj" fmla="val ${Math.max(0, Math.min(50_000, Math.round((shape.radiusPt / Math.max(0.01, Math.min(bounds.width * 0.75, bounds.height * 0.75))) * 100_000)))}"/>`
      : "";
  return `<a:prstGeom prst="${preset}"><a:avLst>${radiusAdjustment}</a:avLst></a:prstGeom>`;
}

function gradientFillXml(
  gradient: NonNullable<AuthoredHybridShapePayload["gradient"]>,
  opacity: number
): string {
  return `<a:gradFill rotWithShape="1"><a:gsLst>${gradient.stops
    .map((stop) => `<a:gs pos="${Math.round(stop.position * 100_000)}">${gradientColorXml(stop.color, opacity)}</a:gs>`)
    .join("")}</a:gsLst><a:lin ang="${rotation(gradient.angleDeg - 90)}" scaled="1"/></a:gradFill>`;
}

function shapePaintXml(shape: AuthoredHybridShapePayload, opacity: number): string {
  const isLine = shape.shape === "line";
  const fill = isLine
    ? "<a:noFill/>"
    : shape.gradient
      ? gradientFillXml(shape.gradient, opacity)
      : shape.fill
        ? colorXml(shape.fill, opacity)
        : "<a:noFill/>";
  const lineFill = isLine && shape.gradient
    ? gradientFillXml(shape.gradient, opacity)
    : shape.stroke
      ? colorXml(shape.stroke, opacity)
      : "<a:noFill/>";
  const hasLine = Boolean(shape.stroke || (isLine && shape.gradient));
  const line = hasLine
    ? `<a:ln w="${Math.max(1, Math.round(shape.strokeWidthPt * EMU_PER_POINT))}"${shape.lineCap === "round" ? ' cap="rnd"' : ""}>${lineFill}${shape.dash ? `<a:prstDash val="${shape.dash}"/>` : ""}${shape.lineJoin === "round" ? "<a:round/>" : ""}${shape.endArrow ? `<a:tailEnd type="${shape.endArrow}" w="lg" len="lg"/>` : ""}</a:ln>`
    : "<a:ln><a:noFill/></a:ln>";
  return `${fill}${line}`;
}

function borderLineBounds(
  bounds: AuthoredHybridRect,
  side: "top" | "right" | "bottom" | "left"
): AuthoredHybridRect {
  const hairline = 0.01;
  if (side === "top") return { x: bounds.x, y: bounds.y, width: bounds.width, height: hairline };
  if (side === "bottom") return { x: bounds.x, y: bounds.y + bounds.height, width: bounds.width, height: hairline };
  if (side === "left") return { x: bounds.x, y: bounds.y, width: hairline, height: bounds.height };
  return { x: bounds.x + bounds.width, y: bounds.y, width: hairline, height: bounds.height };
}

function axisAlignedLineBounds(
  bounds: AuthoredHybridRect,
  rotationDeg: number
): AuthoredHybridRect {
  if (Math.abs(rotationDeg) > 0.001 || Math.min(bounds.width, bounds.height) > 3) {
    return bounds;
  }
  const hairline = 0.01;
  if (bounds.width >= bounds.height) {
    return {
      x: bounds.x,
      y: bounds.y + bounds.height / 2,
      width: bounds.width,
      height: hairline,
    };
  }
  return {
    x: bounds.x + bounds.width / 2,
    y: bounds.y,
    width: hairline,
    height: bounds.height,
  };
}

function lineShapeForBounds(
  shape: AuthoredHybridShapePayload,
  bounds: AuthoredHybridRect
): AuthoredHybridShapePayload {
  if (shape.shape !== "line" || !shape.gradient) return shape;
  return {
    ...shape,
    strokeWidthPt: Math.max(
      0.75,
      shape.strokeWidthPt,
      Math.min(bounds.width, bounds.height) * 0.75
    ),
  };
}

function shiftedShadowBounds(
  bounds: AuthoredHybridRect,
  layer: NonNullable<AuthoredHybridShapePayload["shadowLayers"]>[number]
): AuthoredHybridRect {
  const width = Math.max(0.01, bounds.width + layer.spreadPx * 2);
  const height = Math.max(0.01, bounds.height + layer.spreadPx * 2);
  return {
    x: bounds.x + layer.offsetXPx - layer.spreadPx,
    y: bounds.y + layer.offsetYPx - layer.spreadPx,
    width,
    height,
  };
}

function shadowShape(
  shape: AuthoredHybridShapePayload,
  color: AuthoredHybridColor,
  bounds: AuthoredHybridRect
): AuthoredHybridShapePayload {
  const isLine = shape.shape === "line";
  return {
    ...shape,
    fill: isLine ? null : color,
    gradient: undefined,
    stroke: isLine ? color : null,
    strokeWidthPt: isLine
      ? Math.max(0.75, shape.strokeWidthPt, Math.min(bounds.width, bounds.height) * 0.75)
      : 0,
    borderLines: undefined,
    shadowLayers: undefined,
    dash: undefined,
    endArrow: undefined,
  };
}

function ellipseBorderArcPoints(
  side: "top" | "right" | "bottom" | "left"
): Array<{ x: number; y: number }> {
  const angles: Record<typeof side, [number, number]> = {
    top: [225, 315],
    right: [-45, 45],
    bottom: [45, 135],
    left: [135, 225],
  };
  const [start, end] = angles[side];
  return Array.from({ length: 9 }, (_, index) => {
    const radians = (start + ((end - start) * index) / 8) * Math.PI / 180;
    return {
      x: 0.5 + Math.cos(radians) * 0.5,
      y: 0.5 + Math.sin(radians) * 0.5,
    };
  });
}

function shapeHasBaseObject(shape: AuthoredHybridShapePayload): boolean {
  return Boolean(shape.fill || shape.gradient || shape.stroke || !shape.borderLines?.length);
}

function shapeBaseObjectCount(shape: AuthoredHybridShapePayload): number {
  if (!shapeHasBaseObject(shape)) return 0;
  return 1;
}

export function preparedNativeElementNonVisualIdCount(
  item: PreparedNativeElement
): number {
  return preparedNativeElementUnderlayNonVisualIdCount(item) +
    preparedNativeElementOverlayNonVisualIdCount(item);
}

function textContainerShapeItem(
  item: Extract<PreparedNativeElement, { kind: "text" }>
): Extract<PreparedNativeElement, { kind: "shape" }> | null {
  const container = item.source.text.containerShape;
  if (!container) return null;
  const source: AuthoredHybridNativeShapeElement = {
    id: `${item.source.id}-container`,
    domPath: item.source.domPath,
    tagName: item.source.tagName,
    sourceIndex: item.source.sourceIndex,
    zOrder: item.source.zOrder,
    cssZIndex: item.source.cssZIndex,
    bounds: container.bounds,
    rotationDeg: item.source.rotationDeg,
    opacity: item.source.opacity,
    classification: {
      mode: "native",
      kind: "shape",
      confidence: "safe",
    },
    shape: container.shape,
  };
  return { kind: "shape", source };
}

function transparentTextOverlayItem(
  item: Extract<PreparedNativeElement, { kind: "text" }>
): Extract<PreparedNativeElement, { kind: "text" }> {
  const container = item.source.text.containerShape;
  if (!container) return item;
  return {
    kind: "text",
    source: {
      ...item.source,
      text: {
        ...item.source.text,
        containerShape: {
          ...container,
          shape: {
            ...container.shape,
            fill: null,
            gradient: undefined,
            stroke: null,
            strokeWidthPt: 0,
            borderLines: undefined,
          },
        },
      },
    },
  };
}

export function preparedNativeElementUnderlayNonVisualIdCount(
  item: PreparedNativeElement
): number {
  const shapeItem = item.kind === "shape"
    ? item
    : item.kind === "text"
      ? textContainerShapeItem(item)
      : null;
  if (!shapeItem) return 0;
  return (shapeItem.source.shape.shadowLayers?.length ?? 0) +
    shapeBaseObjectCount(shapeItem.source.shape) +
    (shapeItem.source.shape.borderLines?.length ?? 0);
}

function preparedNativeElementOverlayNonVisualIdCount(
  item: PreparedNativeElement
): number {
  return item.kind === "shape" ? 0 : 1;
}

function powerPointWrapMode(
  element: AuthoredHybridEditableTextElement
): "none" | "square" {
  if (element.text.style.wrapMode === "no-wrap") return "none";

  const hasCapturedLineBreak = !collapseCapturedLineBreaks(element) &&
    element.text.runs.some((run) => run.text.includes("\n"));
  const lineHeightPx = element.text.style.lineHeight.points / 0.75;
  const isSingleLineHeight =
    !hasCapturedLineBreak &&
    element.bounds.px.height <= Math.max(lineHeightPx * 1.45, 1);

  // A one-line browser box has no vertical room for a second PowerPoint line.
  // If a substitute font is a little wider, allowing PowerPoint to wrap here
  // creates a new line outside the authored box and collides with the content
  // below it. Keep only those tight, browser-confirmed single-line boxes on one
  // line. Captured multi-line text and taller body boxes retain square wrapping
  // so they can still absorb cross-platform font-metric differences safely.
  return isSingleLineHeight ? "none" : "square";
}

function nonVisualName(element: AuthoredHybridElement): string {
  return `Presenton hybrid ${element.classification.mode === "native" ? element.classification.kind : "raster"} ${element.id}`;
}

export function serializePreparedNativeElement(
  item: PreparedNativeElement,
  nonVisualId: number,
  relationshipId?: string
): string {
  if (item.kind === "text") {
    const element = item.source;
    const name = escapeXml(nonVisualName(element));
    const anchor = { top: "t", middle: "ctr", bottom: "b" }[
      element.text.style.verticalAlignment
    ];
    const wrap = powerPointWrapMode(element);
    const container = element.text.containerShape;
    const shapeBounds = container?.bounds.px ??
      powerPointSafeTextBounds(element, element.bounds.px);
    const textBounds = element.bounds.px;
    const capturedInsets = container
      ? {
          left: Math.max(0, textBounds.x - shapeBounds.x),
          top: Math.max(0, textBounds.y - shapeBounds.y),
          right: Math.max(0, shapeBounds.x + shapeBounds.width - textBounds.x - textBounds.width),
          bottom: Math.max(0, shapeBounds.y + shapeBounds.height - textBounds.y - textBounds.height),
        }
      : { left: 0, top: 0, right: 0, bottom: 0 };
    const insets = powerPointSafeTextInsets(
      element,
      shapeBounds,
      capturedInsets
    );
    const geometry = container
      ? shapeGeometryXml(container.shape, shapeBounds)
      : '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>';
    const paint = container
      ? shapePaintXml(container.shape, element.opacity)
      : "<a:noFill/><a:ln><a:noFill/></a:ln>";
    return `<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId}" name="${name}"/>${container ? "<p:cNvSpPr/>" : '<p:cNvSpPr txBox="1"/>'}<p:nvPr/></p:nvSpPr><p:spPr>${transformRectXml(shapeBounds, element.rotationDeg)}${geometry}${paint}</p:spPr><p:txBody><a:bodyPr wrap="${wrap}" horzOverflow="clip" vertOverflow="clip" anchor="${anchor}" lIns="${emu(insets.left)}" tIns="${emu(insets.top)}" rIns="${emu(insets.right)}" bIns="${emu(insets.bottom)}"><a:noAutofit/></a:bodyPr><a:lstStyle/>${paragraphsXml(element)}</p:txBody></p:sp>`;
  }
  if (item.kind === "shape") {
    const element = item.source;
    const name = escapeXml(nonVisualName(element));
    const shapes: string[] = [];
    let idOffset = 0;
    element.shape.shadowLayers?.forEach((layer, index) => {
      const bounds = shiftedShadowBounds(element.bounds.px, layer);
      const paintShape = shadowShape(element.shape, layer.color, bounds);
      shapes.push(`<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId + idOffset}" name="${name} shadow ${index + 1}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>${transformRectXml(bounds, element.rotationDeg)}${shapeGeometryXml(paintShape, bounds)}${shapePaintXml(paintShape, element.opacity)}</p:spPr></p:sp>`);
      idOffset += 1;
    });
    if (shapeHasBaseObject(element.shape)) {
      const bounds = element.shape.shape === "line"
        ? axisAlignedLineBounds(element.bounds.px, element.rotationDeg)
        : element.bounds.px;
      const paintShape = lineShapeForBounds(element.shape, element.bounds.px);
      shapes.push(`<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId + idOffset}" name="${name}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>${transformRectXml(bounds, element.rotationDeg)}${shapeGeometryXml(paintShape, bounds)}${shapePaintXml(paintShape, element.opacity)}</p:spPr></p:sp>`);
      idOffset += 1;
    }
    element.shape.borderLines?.forEach((border) => {
      const lineShape: AuthoredHybridShapePayload = {
        shape: element.shape.shape === "ellipse" ? "freeform" : "line",
        fill: null,
        stroke: border.color,
        strokeWidthPt: border.widthPt,
        dash: border.dash,
        lineCap: element.shape.shape === "ellipse" ? "round" : undefined,
        lineJoin: element.shape.shape === "ellipse" ? "round" : undefined,
        radiusPt: 0,
        points: element.shape.shape === "ellipse"
          ? ellipseBorderArcPoints(border.side)
          : undefined,
        closed: false,
      };
      const bounds = element.shape.shape === "ellipse"
        ? element.bounds.px
        : borderLineBounds(element.bounds.px, border.side);
      shapes.push(`<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId + idOffset}" name="${name} ${border.side} border"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>${transformRectXml(bounds, element.rotationDeg)}${shapeGeometryXml(lineShape, bounds)}${shapePaintXml(lineShape, element.opacity)}</p:spPr></p:sp>`);
      idOffset += 1;
    });
    return shapes.join("");
  }
  if (!relationshipId) throw new Error("Native image relationship is missing.");
  const element = item.source;
  const name = escapeXml(nonVisualName(element));
  const imageAlpha = Math.max(0, Math.min(100_000, Math.round(element.opacity * 100_000)));
  return `<p:pic><p:nvPicPr><p:cNvPr id="${nonVisualId}" name="${name}" descr="${escapeXml(element.image.alt)}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${relationshipId}">${imageAlpha < 100_000 ? `<a:alphaModFix amt="${imageAlpha}"/>` : ""}</a:blip><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>${transformXml(element)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:pic>`;
}

export function serializePreparedNativeElementUnderlay(
  item: PreparedNativeElement,
  nonVisualId: number
): string {
  if (item.kind === "shape") {
    return serializePreparedNativeElement(item, nonVisualId);
  }
  if (item.kind !== "text") return "";
  const containerItem = textContainerShapeItem(item);
  return containerItem
    ? serializePreparedNativeElement(containerItem, nonVisualId)
    : "";
}

export function serializePreparedNativeElementOverlay(
  item: PreparedNativeElement,
  nonVisualId: number,
  relationshipId?: string
): string {
  if (item.kind === "shape") return "";
  return serializePreparedNativeElement(
    item.kind === "text" ? transparentTextOverlayItem(item) : item,
    nonVisualId,
    relationshipId
  );
}

function enforceMinimumTextStyle(
  style: AuthoredHybridTextStyle
): AuthoredHybridTextStyle {
  if (style.fontSizePt >= MIN_EDITABLE_FONT_SIZE_PT) return style;
  const fontSizePt = MIN_EDITABLE_FONT_SIZE_PT;
  return {
    ...style,
    fontSizePt,
    lineHeight: {
      ...style.lineHeight,
      points: Math.max(
        style.lineHeight.points,
        fontSizePt * style.lineHeight.multiple
      ),
    },
  };
}

function enforceMinimumTextSize<T extends AuthoredHybridEditableTextElement>(
  element: T
): T {
  return {
    ...element,
    text: {
      ...element.text,
      style: enforceMinimumTextStyle(element.text.style),
      runs: element.text.runs.map((run) => ({
        ...run,
        style: enforceMinimumTextStyle(run.style),
      })),
    },
  } as T;
}
