import { colorWithOpacity, cssNumber, escapeAttribute } from "./css";
import {
  polygonBox,
  polygonClosed,
  polygonPoints,
  polygonSourcePoints,
  vectorShape,
} from "./geometry";
import {
  frameStyle,
  frameStyleFromBox,
  transformStyle,
  vectorEffectStyle,
} from "./style";
import type { JsonRecord, RenderMode } from "./types";
import { readArray, readNumber, readRecord, readString, readStringValue } from "./values";

export function renderPolygon(item: JsonRecord, mode: RenderMode): string {
  if (readString(item.type) === "vector" && vectorShape(item) === "ellipse") {
    return renderEllipseVector(item, mode);
  }

  const points = polygonPoints(item);
  if (points.length < 2) return "";

  const box = polygonBox(item, points);
  const closed = polygonClosed(item, points);
  const stroke = readRecord(item.stroke);
  const fill = readRecord(item.fill);
  const fillColor = closed
    ? colorWithOpacity(readString(fill.color) ?? "", readNumber(fill.opacity))
    : "";
  const strokeWidth = Math.max(0, readNumber(stroke.width) ?? 1);
  const strokeColor = colorWithOpacity(
    readString(stroke.color) ?? (!closed ? "#000000" : ""),
    readNumber(stroke.opacity)
  );
  if (!fillColor && !(strokeColor && strokeWidth > 0)) return "";

  const pointString = points
    .map((point) => `${cssNumber(point.x - box.x)},${cssNumber(point.y - box.y)}`)
    .join(" ");
  const dash = readArray(stroke.dash)
    .map(readNumber)
    .filter((value): value is number => value != null)
    .join(" ");
  const shape = closed
    ? `<polygon points="${escapeAttribute(pointString)}"${fillColor ? ` fill="${escapeAttribute(fillColor)}"` : ` fill="none"`}${strokeColor && strokeWidth > 0
      ? ` stroke="${escapeAttribute(strokeColor)}" stroke-width="${cssNumber(strokeWidth)}"`
      : ""
    }${dash ? ` stroke-dasharray="${dash}"` : ""}/>`
    : `<polyline points="${escapeAttribute(pointString)}" fill="none"${strokeColor && strokeWidth > 0
      ? ` stroke="${escapeAttribute(strokeColor)}" stroke-width="${cssNumber(strokeWidth)}"`
      : ""
    }${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
  return `<div style="${frameStyleFromBox(box, mode)}${transformStyle(
    item
  )}${vectorEffectStyle(item)}overflow:visible"><svg width="100%" height="100%" viewBox="0 0 ${cssNumber(
    box.width ?? 1
  )} ${cssNumber(
    box.height ?? 1
  )}" preserveAspectRatio="none" style="display:block;overflow:visible">${shape}</svg></div>`;
}

function renderEllipseVector(item: JsonRecord, mode: RenderMode): string {
  const points = polygonSourcePoints(item);
  if (points.length < 2) return "";

  const box = polygonBox(item, points);
  const stroke = readRecord(item.stroke);
  const fill = readRecord(item.fill);
  const fillColor = colorWithOpacity(
    readString(fill.color) ?? "",
    readNumber(fill.opacity)
  );
  const strokeWidth = Math.max(0, readNumber(stroke.width) ?? 1);
  const strokeColor = colorWithOpacity(
    readString(stroke.color) ?? "",
    readNumber(stroke.opacity)
  );
  if (!fillColor && !(strokeColor && strokeWidth > 0)) return "";

  const dash = readArray(stroke.dash)
    .map(readNumber)
    .filter((value): value is number => value != null)
    .join(" ");
  const width = box.width ?? 1;
  const height = box.height ?? 1;
  const shape = `<ellipse cx="${cssNumber(width / 2)}" cy="${cssNumber(
    height / 2
  )}" rx="${cssNumber(width / 2)}" ry="${cssNumber(height / 2)}"${fillColor
    ? ` fill="${escapeAttribute(fillColor)}"`
    : ` fill="none"`}${strokeColor && strokeWidth > 0
    ? ` stroke="${escapeAttribute(strokeColor)}" stroke-width="${cssNumber(strokeWidth)}"`
    : ""
  }${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;

  return `<div style="${frameStyleFromBox(box, mode)}${transformStyle(
    item
  )}${vectorEffectStyle(item)}overflow:visible"><svg width="100%" height="100%" viewBox="0 0 ${cssNumber(
    width
  )} ${cssNumber(
    height
  )}" preserveAspectRatio="none" style="display:block;overflow:visible">${shape}</svg></div>`;
}

export function renderSvg(item: JsonRecord, mode: RenderMode): string {
  const svg = readStringValue(item.svg);
  if (!svg) return "";
  return `<div style="${frameStyle(item, mode)}${transformStyle(
    item
  )}overflow:hidden">${svg}</div>`;
}
