import { normalizeChartColor } from "./chart-colors";
import { cssNumber, cssUrl, escapeAttribute, escapeCssColor } from "./css";
import { boxStyle, frameStyle } from "./style";
import type { JsonRecord, RenderMode } from "./types";
import { clamp, readArray, readBoolean, readNumber, readString } from "./values";

export function renderImage(item: JsonRecord, mode: RenderMode): string {
  const source = readString(item.data);
  if (!source) return "";
  const color = normalizeChartColor(readString(item.color));
  const clipPath = clipPathStyle(item);
  if (color && readBoolean(item.isIcon ?? item.is_icon)) {
    const maskUrl = cssUrl(source);
    const maskSize = imageMaskSize(item.fit);
    return `<div style="${frameStyle(item, mode)}${boxStyle(
      item
    )}color:${escapeCssColor(
      color
    )};background:currentColor;-webkit-mask:${maskUrl} center/${maskSize} no-repeat;mask:${maskUrl} center/${maskSize} no-repeat;${clipPath}"></div>`;
  }
  const fit = imageFit(item.fit);
  const focusStyle = imageFocusStyle(item);
  const cropTransformStyle = imageCropTransformStyle(item);
  if (cropTransformStyle) {
    return `<div style="${frameStyle(item, mode)}${boxStyle(
      item
    )}${clipPath}overflow:hidden;"><img alt="" src="${escapeAttribute(
      source
    )}" style="display:block;max-width:none;max-height:none;height:100%;width:100%;object-fit:${fit};${focusStyle}${cropTransformStyle}"></div>`;
  }
  if (clipPath) {
    return `<div style="${frameStyle(item, mode)}${boxStyle(
      item
    )}${clipPath}overflow:hidden;"><img alt="" src="${escapeAttribute(
      source
    )}" style="display:block;max-width:none;max-height:none;height:100%;width:100%;object-fit:${fit};${focusStyle}"></div>`;
  }
  return `<img alt="" src="${escapeAttribute(source)}" style="${frameStyle(
    item,
    mode
  )}${boxStyle(
    item
  )}display:block;max-width:none;max-height:none;object-fit:${fit};${focusStyle}${clipPath}">`;
}

function imageFit(value: unknown): string {
  return value === "cover" || value === "fill" ? value : "contain";
}

function imageMaskSize(value: unknown): string {
  const fit = imageFit(value);
  return fit === "fill" ? "100% 100%" : fit;
}

function imageCropScale(item: JsonRecord): number {
  const value = readNumber(item.crop_scale ?? item.cropScale);
  if (value == null) return 1;
  return clamp(value, 0.1, 6);
}

function imageCropTransformStyle(item: JsonRecord): string {
  const cropScale = imageCropScale(item);
  if (Math.abs(cropScale - 1) <= 0.01) return "";
  return `transform:scale(${cssNumber(cropScale)});transform-origin:${
    imageFocusValue(item) ?? "center"
  };`;
}

function imageFocusStyle(item: JsonRecord): string {
  const focus = imageFocusValue(item);
  return focus ? `object-position:${focus};` : "";
}

function imageFocusValue(item: JsonRecord): string | null {
  const focus = readArray(item.focus);
  const rawX = item.focus_x ?? item.focusX ?? focus[0];
  const rawY = item.focus_y ?? item.focusY ?? focus[1];
  if (rawX == null && rawY == null) return null;

  const focusX = clamp(readNumber(rawX) ?? 50, 0, 100);
  const focusY = clamp(readNumber(rawY) ?? 50, 0, 100);
  return `${cssNumber(focusX)}% ${cssNumber(focusY)}%`;
}

function clipPathStyle(item: JsonRecord): string {
  const value = normalizeCssClipPath(
    readString(item.clippath ?? item.clipPath ?? item.clip_path)
  );
  return value ? `clip-path:${value};-webkit-clip-path:${value};` : "";
}

function normalizeCssClipPath(value: string | null): string | null {
  if (!value) return null;

  let normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized || normalized.toLowerCase() === "none") return null;

  const doubleQuotedPath = normalized.match(/^path\("([^"]*)"\)$/i);
  if (doubleQuotedPath) {
    normalized = `path('${doubleQuotedPath[1]}')`;
  }

  if (/[";{}<>\\]/.test(normalized)) return null;
  if (!/^[a-zA-Z0-9\s.,%()+\-_' ]+$/.test(normalized)) return null;

  const functionName = normalized.match(/^([a-z-]+)\(/i)?.[1]?.toLowerCase();
  if (
    !functionName ||
    !["path", "polygon", "circle", "ellipse", "inset"].includes(functionName) ||
    !normalized.endsWith(")")
  ) {
    return null;
  }

  if (functionName === "path" && !/^path\('[^']*'\)$/i.test(normalized)) {
    return null;
  }

  return normalized;
}
