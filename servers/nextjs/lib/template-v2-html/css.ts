import type { JsonRecord } from "./types";
import { readNumber } from "./values";

export function paddingStyle(padding: JsonRecord): string {
  return `padding:${cssNumber(readNumber(padding.top) ?? 0)}px ${cssNumber(
    readNumber(padding.right) ?? 0
  )}px ${cssNumber(readNumber(padding.bottom) ?? 0)}px ${cssNumber(
    readNumber(padding.left) ?? 0
  )}px;`;
}

export function borderRadiusStyle(radius: JsonRecord): string {
  const tl = readNumber(radius.tl) ?? 0;
  const tr = readNumber(radius.tr) ?? tl;
  const br = readNumber(radius.br) ?? tl;
  const bl = readNumber(radius.bl) ?? tl;
  return tl || tr || br || bl
    ? `${cssNumber(tl)}px ${cssNumber(tr)}px ${cssNumber(br)}px ${cssNumber(bl)}px`
    : "";
}

export function horizontalAlign(value: string | null): string {
  return value === "center" ? "center" : value === "right" ? "flex-end" : "flex-start";
}

export function verticalAlign(value: string | null): string {
  return value === "middle" || value === "center"
    ? "center"
    : value === "bottom"
      ? "flex-end"
      : "flex-start";
}

export function textAlign(value: string | null): string {
  return value === "center" || value === "right" ? value : "left";
}

export function cssAlignment(value: string | null, fallback: string): string {
  return value === "flex-start" ||
    value === "flex-end" ||
    value === "center" ||
    value === "stretch"
    ? value
    : fallback;
}

export function normalizeCssColor(color: string): string {
  const normalized = color.trim();
  const hex = normalized.match(/^#?([0-9a-fA-F]{6})$/)?.[1];
  return hex ? `#${hex}` : normalized;
}

export function colorWithOpacity(color: string, opacity: number | null): string {
  const normalized = normalizeCssColor(color);
  if (opacity == null || opacity >= 1 || normalized === "transparent") return normalized;
  const hex = normalized.replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(hex)) return normalized;
  const value = Number.parseInt(hex, 16);
  return `rgba(${(value >> 16) & 255},${(value >> 8) & 255},${value & 255},${Math.max(
    0,
    opacity
  )})`;
}

export function cssNumber(value: number): string {
  return Number.isFinite(value) ? String(value) : "0";
}

export function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function escapeAttribute(value: string): string {
  return escapeHtml(value);
}

export function escapeScriptText(value: string): string {
  return value.replaceAll("</script", "<\\/script").replaceAll("<!--", "<\\!--");
}

export function escapeStyleText(value: string): string {
  return value.replaceAll("</style", "<\\/style");
}

export function escapeCssColor(value: string): string {
  return /^[#(),.%\s\w-]+$/.test(value) ? value : "transparent";
}

export function escapeCssFont(value: string): string {
  return `'${value.replaceAll("\\", "\\\\").replaceAll("'", "\\'")}'`;
}

export function cssUrl(value: string): string {
  return `url('${escapeCssUrl(value)}')`;
}

export function escapeCssUrl(value: string): string {
  return value
    .replaceAll("\\", "\\\\")
    .replaceAll('"', '\\"')
    .replaceAll("'", "\\'")
    .replaceAll("\n", "")
    .replaceAll("\r", "");
}
