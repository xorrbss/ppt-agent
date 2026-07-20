import sharp from "sharp";

import type {
  AuthoredHybridColor,
  AuthoredHybridElement,
  AuthoredHybridNativeImageElement,
  AuthoredHybridNativeShapeElement,
  AuthoredHybridNativeTextElement,
  AuthoredHybridTextStyle,
} from "./schema.ts";
import { validateHybridDataImageUrl } from "./security.ts";

export const EMU_PER_CSS_PX = 9_525;
const EMU_PER_POINT = 12_700;
const MAX_NATIVE_ELEMENTS_PER_SLIDE = 200;
const MAX_IMAGE_PIXELS = 40_000_000;
const MAX_TEXT_CODE_POINTS = 100_000;

export type PreparedNativeElement =
  | { kind: "text"; source: AuthoredHybridNativeTextElement }
  | { kind: "shape"; source: AuthoredHybridNativeShapeElement }
  | {
      kind: "image";
      source: AuthoredHybridNativeImageElement;
      png: Buffer;
    };

type AuthoredHybridNativeElement =
  | AuthoredHybridNativeTextElement
  | AuthoredHybridNativeImageElement
  | AuthoredHybridNativeShapeElement;

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
  return (
    [x, y, width, height, element.rotationDeg, element.opacity].every(Number.isFinite) &&
    x >= -0.01 &&
    y >= -0.01 &&
    width > 0 &&
    height > 0 &&
    x + width <= 1280.01 &&
    y + height <= 720.01 &&
    element.opacity >= 0 &&
    element.opacity <= 1
  );
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
  elements: readonly AuthoredHybridElement[]
): Promise<PreparedNativeElement[]> {
  const candidates = elements
    .filter(
      (element): element is AuthoredHybridNativeElement =>
        element.classification.mode === "native"
    )
    .slice(0, MAX_NATIVE_ELEMENTS_PER_SLIDE);
  const prepared: PreparedNativeElement[] = [];
  for (const candidate of candidates) {
    if (!boundsAreSafe(candidate)) continue;
    try {
      if ("text" in candidate) {
        if (
          !xmlTextIsSafe(candidate.text.plainText) ||
          !styleIsSafe(candidate.text.style) ||
          candidate.text.runs.some(
            (run) => !xmlTextIsSafe(run.text) || !styleIsSafe(run.style)
          )
        ) {
          continue;
        }
        prepared.push({ kind: "text", source: candidate });
      } else if ("shape" in candidate) {
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
  allowedIds: ReadonlySet<string> = new Set(prepared.map((item) => item.source.id))
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

function transformXml(element: AuthoredHybridElement): string {
  const rect = element.bounds.px;
  return `<a:xfrm${rotation(element.rotationDeg) ? ` rot="${rotation(element.rotationDeg)}"` : ""}><a:off x="${emu(rect.x)}" y="${emu(rect.y)}"/><a:ext cx="${emu(rect.width)}" cy="${emu(rect.height)}"/></a:xfrm>`;
}

function alphaAmount(color: AuthoredHybridColor, opacity: number): number {
  return Math.max(0, Math.min(100_000, Math.round(color.alpha * opacity * 100_000)));
}

function colorXml(color: AuthoredHybridColor, opacity: number): string {
  const alpha = alphaAmount(color, opacity);
  return `<a:solidFill><a:srgbClr val="${color.hex}">${alpha < 100_000 ? `<a:alpha val="${alpha}"/>` : ""}</a:srgbClr></a:solidFill>`;
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
    `sz="${Math.round(style.fontSizePt * 100)}"`,
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

function textSegments(element: AuthoredHybridNativeTextElement): TextSegment[] {
  const joined = element.text.runs.map((run) => run.text).join("");
  if (element.text.runs.length && joined === element.text.plainText) {
    return element.text.runs.map((run) => ({ text: run.text, style: run.style }));
  }
  return [{ text: element.text.plainText, style: element.text.style }];
}

function paragraphsXml(element: AuthoredHybridNativeTextElement): string {
  const alignment = {
    left: "l",
    center: "ctr",
    right: "r",
    justify: "just",
  }[element.text.style.horizontalAlignment];
  const pPr = `<a:pPr algn="${alignment}"${element.text.style.direction === "rtl" ? ` rtl="1"` : ""}><a:lnSpc><a:spcPts val="${Math.max(0, Math.round(element.text.style.lineHeight.points * 100))}"/></a:lnSpc></a:pPr>`;
  const paragraphs: string[][] = [[]];
  for (const segment of textSegments(element)) {
    const pieces = segment.text.split("\n");
    pieces.forEach((piece, index) => {
      if (index > 0) paragraphs.push([]);
      if (piece || pieces.length === 1) {
        paragraphs.at(-1)?.push(
          `<a:r>${runPropertiesXml(segment.style, element.opacity)}<a:t xml:space="preserve">${escapeXml(piece)}</a:t></a:r>`
        );
      }
    });
  }
  return paragraphs
    .map((runs) => `<a:p>${pPr}${runs.join("")}<a:endParaRPr lang="ko-KR"/></a:p>`)
    .join("");
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
    return `<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId}" name="${name}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>${transformXml(element)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square" anchor="${anchor}" lIns="0" tIns="0" rIns="0" bIns="0"><a:noAutofit/></a:bodyPr><a:lstStyle/>${paragraphsXml(element)}</p:txBody></p:sp>`;
  }
  if (item.kind === "shape") {
    const element = item.source;
    const name = escapeXml(nonVisualName(element));
    const preset =
      element.shape.shape === "round-rectangle"
        ? "roundRect"
        : element.shape.shape === "ellipse"
          ? "ellipse"
          : element.shape.shape === "line"
            ? "line"
            : "rect";
    const radiusAdjustment =
      preset === "roundRect"
        ? `<a:gd name="adj" fmla="val ${Math.max(0, Math.min(50_000, Math.round((element.shape.radiusPt / Math.max(0.01, Math.min(element.bounds.px.width * 0.75, element.bounds.px.height * 0.75))) * 100_000)))}"/>`
        : "";
    const fill = element.shape.fill
      ? colorXml(element.shape.fill, element.opacity)
      : "<a:noFill/>";
    const line = element.shape.stroke
      ? `<a:ln w="${Math.max(1, Math.round(element.shape.strokeWidthPt * EMU_PER_POINT))}">${colorXml(element.shape.stroke, element.opacity)}</a:ln>`
      : "<a:ln><a:noFill/></a:ln>";
    return `<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId}" name="${name}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>${transformXml(element)}<a:prstGeom prst="${preset}"><a:avLst>${radiusAdjustment}</a:avLst></a:prstGeom>${fill}${line}</p:spPr></p:sp>`;
  }
  if (!relationshipId) throw new Error("Native image relationship is missing.");
  const element = item.source;
  const name = escapeXml(nonVisualName(element));
  const imageAlpha = Math.max(0, Math.min(100_000, Math.round(element.opacity * 100_000)));
  return `<p:pic><p:nvPicPr><p:cNvPr id="${nonVisualId}" name="${name}" descr="${escapeXml(element.image.alt)}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${relationshipId}">${imageAlpha < 100_000 ? `<a:alphaModFix amt="${imageAlpha}"/>` : ""}</a:blip><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>${transformXml(element)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:pic>`;
}
