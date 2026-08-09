import {
  borderRadiusStyle,
  colorWithOpacity,
  cssNumber,
  escapeCssColor,
  escapeCssFont,
} from "./css";
import { polygonBox, polygonPoints, polygonSourcePoints, vectorShape } from "./geometry";
import type { Box, JsonRecord, RenderMode } from "./types";
import { clamp, hasOwn, readBoolean, readNumber, readRecord, readString } from "./values";

export function frameStyle(
  item: JsonRecord,
  mode: RenderMode,
  fallbackSize?: { width: number; height: number }
): string {
  const box = readBox(item, fallbackSize);
  return frameStyleFromBox(box, mode);
}

export function frameStyleFromBox(box: Box, mode: RenderMode): string {
  let style = `box-sizing:border-box;min-height:0;min-width:0;position:${mode === "absolute" ? "absolute" : "relative"
    };`;
  if (mode === "flow") style += "flex-shrink:0;";
  if (mode === "absolute") {
    style += `left:${cssNumber(box.x)}px;top:${cssNumber(box.y)}px;`;
  }
  if (box.width != null) style += `width:${cssNumber(box.width)}px;`;
  if (box.height != null) style += `height:${cssNumber(box.height)}px;`;
  return style;
}

export function readBox(
  item: JsonRecord,
  fallbackSize?: { width: number; height: number }
): Box {
  const position = readRecord(item.position);
  const size = readRecord(item.size);
  const type = readString(item.type);
  if (type === "vector") {
    return polygonBox(
      item,
      vectorShape(item) === "ellipse"
        ? polygonSourcePoints(item)
        : polygonPoints(item)
    );
  }
  return {
    x: readNumber(position.x) ?? 0,
    y: readNumber(position.y) ?? 0,
    width: readNumber(size.width) ?? fallbackSize?.width,
    height: readNumber(size.height) ?? fallbackSize?.height,
  };
}

export function childrenBounds(
  children: JsonRecord[]
): { width: number; height: number } {
  return children.reduce<{ width: number; height: number }>(
    (bounds, child) => {
      const box = readBox(child);
      return {
        width: Math.max(bounds.width, box.x + (box.width ?? 1)),
        height: Math.max(bounds.height, box.y + (box.height ?? 1)),
      };
    },
    { width: 1, height: 1 }
  );
}

export function boxStyle(item: JsonRecord): string {
  const fill = readRecord(item.fill);
  const stroke = readRecord(item.stroke);
  const shadow = readRecord(item.shadow);
  const radius = readRecord(item.borderRadius ?? item.border_radius);
  let style = transformStyle(item);
  const fillColor = readString(fill.color);
  if (fillColor) {
    style += `background-color:${escapeCssColor(
      colorWithOpacity(fillColor, readNumber(fill.opacity))
    )};`;
  }
  const strokeColor = readString(stroke.color);
  const strokeWidth = readNumber(stroke.width);
  if (strokeColor || strokeWidth != null) {
    style += `border:${cssNumber(strokeWidth ?? 1)}px solid ${escapeCssColor(
      colorWithOpacity(strokeColor ?? "transparent", readNumber(stroke.opacity))
    )};`;
  }
  const borderRadius = borderRadiusStyle(radius);
  if (borderRadius) style += `border-radius:${borderRadius};`;
  const shadowValue = shadowCssValue(shadow);
  if (shadowValue) style += `box-shadow:${shadowValue};`;
  const opacity = readNumber(item.opacity);
  if (opacity != null) style += `opacity:${cssNumber(opacity)};`;
  return style;
}

export function textShadowStyle(item: JsonRecord): string {
  const shadowValue = shadowCssValue(readRecord(item.shadow));
  return shadowValue ? `text-shadow:${shadowValue};` : "";
}

export function vectorEffectStyle(item: JsonRecord): string {
  const shadowValue = shadowCssValue(readRecord(item.shadow));
  const opacity = readNumber(item.opacity);
  return `${shadowValue ? `filter:drop-shadow(${shadowValue});` : ""}${
    opacity != null ? `opacity:${cssNumber(clamp(opacity, 0, 1))};` : ""
  }`;
}

function shadowCssValue(shadow: JsonRecord): string {
  const shadowOpacity = Object.keys(shadow).length
    ? (readNumber(shadow.opacity) ?? 1)
    : 0;
  if (shadowOpacity <= 0) return "";

  return `${cssNumber(readNumber(shadow.offsetX ?? shadow.offset_x) ?? 0)}px ${cssNumber(
    readNumber(shadow.offsetY ?? shadow.offset_y) ?? 0
  )}px ${cssNumber(readNumber(shadow.blur) ?? 0)}px ${escapeCssColor(
    colorWithOpacity(readString(shadow.color) ?? "#000000", shadowOpacity)
  )}`;
}

export function transformStyle(item: JsonRecord): string {
  const rotation = readNumber(item.rotation);
  const flipH = readBoolean(item.flip_h ?? item.flipH);
  const flipV = readBoolean(item.flip_v ?? item.flipV);
  if (!rotation && !flipH && !flipV) return "";

  const transforms = [];
  if (rotation) transforms.push(`rotate(${cssNumber(rotation)}deg)`);
  if (flipH) transforms.push("scaleX(-1)");
  if (flipV) transforms.push("scaleY(-1)");
  return `transform:${transforms.join(" ")};transform-origin:center;`;
}

export function fontStyle(
  fontValue: unknown,
  options: { includeLineHeight?: boolean; includeTextDecoration?: boolean } = {}
): string {
  const font = readRecord(fontValue);
  let style = `color:${escapeCssColor(
    colorWithOpacity(readString(font.color) ?? "#111827", readNumber(font.opacity))
  )};`;
  const family = readString(font.family);
  const size = readNumber(font.size);
  if (family) style += `font-family:${escapeCssFont(family)};`;
  if (size != null) style += `font-size:${cssNumber(size)}px;`;
  if (hasOwn(font, "italic")) {
    style += readBoolean(font.italic) ? "font-style:italic;" : "font-style:normal;";
  }
  if (hasOwn(font, "bold")) {
    style += readBoolean(font.bold) ? "font-weight:700;" : "font-weight:400;";
  }
  if (options.includeLineHeight !== false) style += lineHeightStyle(font);
  const letterSpacing = readNumber(font.letterSpacing ?? font.letter_spacing);
  if (letterSpacing != null) style += `letter-spacing:${cssNumber(letterSpacing)}px;`;
  if (options.includeTextDecoration !== false) {
    style += textDecorationStyle(font);
  }
  return style;
}

function textDecorationStyle(font: JsonRecord): string {
  if (hasOwn(font, "underline")) {
    return readBoolean(font.underline)
      ? "text-decoration:underline;"
      : "text-decoration:none;";
  }

  const decorations = [font.text_decoration, font.textDecoration]
    .map((value) => readString(value)?.toLowerCase())
    .filter(Boolean);
  if (decorations.includes("underline")) return "text-decoration:underline;";
  if (decorations.includes("none")) return "text-decoration:none;";
  return "";
}

export function lineHeightStyle(font: JsonRecord, fallback?: number): string {
  const lineHeight = readNumber(font.lineHeight ?? font.line_height) ?? fallback;
  if (lineHeight == null) return "";

  return `line-height:${cssNumber(lineHeight)};`;
}

export function textOverflowStyle(): string {
  return "overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;";
}
