/**
 * Stage 3 (map to schema) — paint and box decoration shared by every element
 * type: alignment, font, fill, stroke, border radius, padding and shadow.
 */
import type {
  Alignment,
  BorderRadius,
  Fill,
  Font,
  Padding,
  Shadow,
  Stroke,
} from "@/components/slide-editor/types";
import {
  MAX_BORDER_RADIUS,
  clamp,
  clampOptional,
  readArray,
  readBoolean,
  readColor,
  readEnum,
  readNumber,
  readRawNumber,
  readString,
  round,
  stripNullish,
  truncateString,
  type UnknownRecord,
} from "@/components/slide-editor/importing/template-v2/values";

export function adaptAlignment(value: UnknownRecord | null): Alignment | null {
  if (!value) return null;
  return stripNullish({
    horizontal: readEnum(value, ["left", "center", "right"], "horizontal"),
    vertical: readEnum(value, ["top", "middle", "bottom"], "vertical"),
  });
}

export function adaptFont(value: UnknownRecord | null): Font | null {
  if (!value) return null;
  const fontWeight = readNumber(value, "font_weight");
  const size = readNumber(value, "size");
  return stripNullish({
    family:
      truncateString(readString(value.family) ?? readString(value.name) ?? "", 80) ||
      null,
    size: size == null ? null : clamp(round(size), 6, 360),
    color: readColor(value.color),
    bold: readBoolean(value, "bold") ?? (fontWeight == null ? null : fontWeight >= 600),
    italic: readBoolean(value, "italic"),
    underline:
      readBoolean(value, "underline") ??
      ([
        readString(value.text_decoration),
        readString(value.textDecoration),
      ].includes("underline")
        ? true
        : null),
    line_height: clampOptional(readNumber(value, "line_height"), 0.8, 2.2),
    letter_spacing: clamp(readNumber(value, "letter_spacing") ?? 0, -200, 600),
    ellipsis: readBoolean(value, "ellipsis"),
    opacity: clampOptional(readNumber(value, "opacity"), 0, 1),
  });
}

export function adaptFill(value: UnknownRecord | null): Fill | null {
  const color = readColor(value?.color);
  if (!color) return null;
  return {
    ...value,
    color,
    opacity: clamp(readNumber(value ?? {}, "opacity") ?? 1, 0, 1),
  };
}

export function adaptStroke(value: UnknownRecord | null): Stroke | null {
  const color = readColor(value?.color);
  if (!color) return null;
  const stroke: Stroke & UnknownRecord = {
    ...value,
    color,
    opacity: clamp(readNumber(value ?? {}, "opacity") ?? 1, 0, 1),
    width: clamp(round(readNumber(value ?? {}, "width") ?? 1), 0, 8),
  };
  if (value && Object.hasOwn(value, "dash")) {
    stroke.dash = readArray(value, "dash")
      .map((item) => readRawNumber(item))
      .filter((item): item is number => item != null && item >= 0);
  }
  return stroke;
}

export function hasVisiblePaint(
  fill: Fill | null,
  stroke: Stroke | null,
  opacity?: number | null,
) {
  if ((opacity ?? 1) <= 0) return false;
  if (fill && (fill.opacity ?? 1) > 0) return true;
  return Boolean(stroke && stroke.width > 0 && (stroke.opacity ?? 1) > 0);
}

export function adaptBorderRadius(
  value: UnknownRecord | null,
): BorderRadius | null {
  if (!value) return null;
  return {
    tl: clamp(round(readNumber(value, "tl") ?? 0), 0, MAX_BORDER_RADIUS),
    tr: clamp(round(readNumber(value, "tr") ?? 0), 0, MAX_BORDER_RADIUS),
    bl: clamp(round(readNumber(value, "bl") ?? 0), 0, MAX_BORDER_RADIUS),
    br: clamp(round(readNumber(value, "br") ?? 0), 0, MAX_BORDER_RADIUS),
  };
}

export function adaptPadding(value: UnknownRecord | null): Padding | null {
  if (!value) return null;
  return {
    top: Math.max(0, round(readNumber(value, "top") ?? 0)),
    right: Math.max(0, round(readNumber(value, "right") ?? 0)),
    bottom: Math.max(0, round(readNumber(value, "bottom") ?? 0)),
    left: Math.max(0, round(readNumber(value, "left") ?? 0)),
  };
}

export function adaptShadow(value: UnknownRecord | null): Shadow | null {
  if (!value) return null;
  return {
    ...value,
    color: readColor(value.color),
    blur: Math.max(0, readNumber(value, "blur") ?? 0),
    opacity: clamp(readNumber(value, "opacity") ?? 0.2, 0, 1),
    offset_x:
      readNumber(value, "offset_x") ?? readRawNumber(value.offsetX) ?? 0,
    offset_y:
      readNumber(value, "offset_y") ?? readRawNumber(value.offsetY) ?? 0,
  };
}
