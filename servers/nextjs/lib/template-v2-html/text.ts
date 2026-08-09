import { normalizeMathLatex, renderMathHtml } from "@/lib/math";
import {
  cssNumber,
  escapeAttribute,
  escapeCssColor,
  escapeHtml,
  horizontalAlign,
  normalizeCssColor,
  textAlign,
  verticalAlign,
} from "./css";
import { normalizedListRunsForHtml, normalizedRunsForHtml } from "./runs";
import {
  fontStyle,
  frameStyle,
  lineHeightStyle,
  textOverflowStyle,
  textShadowStyle,
  transformStyle,
} from "./style";
import type { JsonRecord, RenderMode } from "./types";
import {
  readArray,
  readBoolean,
  readNumber,
  readRecord,
  readString,
  readStringValue,
} from "./values";

export function renderText(item: JsonRecord, mode: RenderMode): string {
  const font = readRecord(item.font);
  const alignment = readRecord(item.alignment);
  const horizontal = readString(alignment.horizontal);
  const vertical = readString(alignment.vertical);
  const runs = normalizedRunsForHtml(item, font);
  const runHtml = runs
    .map((run) => {
      const runFont = { ...font, ...readRecord(run.font) };
      return `<span style="${fontStyle(runFont)}">${escapeHtml(
        readStringValue(run.text)
      )}</span>`;
    })
    .join("");

  return `<div style="${frameStyle(item, mode)}${transformStyle(item)}${fontStyle(font, {
    includeLineHeight: false,
    includeTextDecoration: false,
  })}${textShadowStyle(item)}display:flex;align-items:${verticalAlign(
    vertical
  )};justify-content:${horizontalAlign(horizontal)};${lineHeightStyle(
    font,
    1.1
  )}${textOverflowStyle()}text-align:${textAlign(horizontal)};"><span style="display:block;width:100%">${runHtml}</span></div>`;
}

export function renderMath(item: JsonRecord, mode: RenderMode): string {
  const latex = normalizeMathLatex(item.latex);
  if (!latex) return "";
  const font = readRecord(item.font);
  const alignment = readRecord(item.alignment);
  const horizontal = readString(alignment.horizontal);
  const vertical = readString(alignment.vertical);
  const displayMode = readBoolean(item.display_mode ?? item.displayMode) ?? true;
  const markup = renderMathHtml(latex, { displayMode });
  const color = normalizeCssColor(readString(font.color) ?? "#111827");
  const fontSize = readNumber(font.size) ?? 32;

  return `<div data-presenton-math="true" data-screenshot="true" data-screenshot-include-children="true" aria-label="${escapeAttribute(
    `Mathematical expression: ${latex}`
  )}" style="${frameStyle(item, mode)}${transformStyle(
    item
  )}display:flex;align-items:${verticalAlign(vertical)};justify-content:${horizontalAlign(
    horizontal
  )};overflow:hidden;color:${escapeCssColor(color)};font-size:${cssNumber(
    fontSize
  )}px;"><div style="max-width:100%;max-height:100%;overflow:hidden;">${markup}</div></div>`;
}

export function renderTextList(item: JsonRecord, mode: RenderMode): string {
  const marker = readString(item.marker);
  const tag = marker === "number" ? "ol" : "ul";
  const font = readRecord(item.font);
  const entries = readArray(item.items)
    .map((entry) => {
      const runs = normalizedListRunsForHtml(entry, font);
      const html = runs
        .map(
          (run) =>
            `<span style="${fontStyle({
              ...font,
              ...readRecord(run.font),
            })}">${escapeHtml(readStringValue(run.text))}</span>`
        )
        .join("");
      return `<li style="${textOverflowStyle()}">${html}</li>`;
    })
    .join("");
  const listStyle = `margin:0;padding-left:${marker === "none" ? 0 : 24}px;${marker === "none" ? "list-style-type:none;" : ""
    }`;

  return `<div style="${frameStyle(item, mode)}${transformStyle(item)}${fontStyle(
    font,
    { includeTextDecoration: false }
  )}${textOverflowStyle()}"><${tag} style="${listStyle}">${entries}</${tag}></div>`;
}
