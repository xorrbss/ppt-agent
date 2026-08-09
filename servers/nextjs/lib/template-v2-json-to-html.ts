import { resolveBackendAssetUrl } from "@/utils/api";
import { hasChartItem } from "./template-v2-html/chart";
import { renderChartScripts } from "./template-v2-html/chart-script";
import { cssNumber, escapeCssColor, normalizeCssColor } from "./template-v2-html/css";
import { renderFontAssetTags } from "./template-v2-html/fonts";
import { renderItem } from "./template-v2-html/render-item";
import type {
  JsonRecord,
  JsonToHtmlItem,
  TemplateV2HtmlOptions,
  TemplateV2RenderPayload,
} from "./template-v2-html/types";
import {
  readArray,
  readRecord,
  readString,
} from "./template-v2-html/values";

export const TEMPLATE_V2_HTML_WIDTH = 1280;
export const TEMPLATE_V2_HTML_HEIGHT = 720;

export function templateV2UiToHtml(
  ui: unknown,
  options: TemplateV2HtmlOptions = {}
): string | null {
  const payload = templateV2RenderPayload(ui, options);
  if (!payload) return null;

  return jsonToHtml(
    payload.items,
    payload.width,
    payload.height,
    payload.fonts,
    payload.background
  );
}

export function templateV2UiToHtmlFragment(
  ui: unknown,
  options: TemplateV2HtmlOptions = {}
): string | null {
  const payload = templateV2RenderPayload(ui, options);
  if (!payload) return null;

  return jsonToHtmlFragment(
    payload.items,
    payload.width,
    payload.height,
    payload.fonts,
    payload.background
  );
}

function templateV2RenderPayload(
  ui: unknown,
  options: TemplateV2HtmlOptions
): TemplateV2RenderPayload | null {
  const record = readRecord(ui);
  const rootElements = readArray(record.elements);
  const components = readArray(record.components);
  const items = [...rootElements, ...components].map((item) =>
    readRecord(normalizeTemplateV2AssetUrls(item))
  );

  if (items.length === 0) {
    return null;
  }

  const width = options.width ?? TEMPLATE_V2_HTML_WIDTH;
  const height = options.height ?? TEMPLATE_V2_HTML_HEIGHT;
  const background = normalizeCssColor(readString(record.background) ?? "#FFFFFF");

  return {
    items,
    width,
    height,
    fonts: options.fonts,
    background,
  };
}

export function hasTemplateV2RenderableUi(ui: unknown): boolean {
  const record = readRecord(ui);
  return readArray(record.components).length > 0 || readArray(record.elements).length > 0;
}

function normalizeTemplateV2AssetUrls(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(normalizeTemplateV2AssetUrls);
  }

  const record = readRecord(value);
  if (!Object.keys(record).length) return value;

  const normalized = Object.fromEntries(
    Object.entries(record).map(([key, child]) => [
      key,
      normalizeTemplateV2AssetUrls(child),
    ])
  ) as JsonRecord;

  if (readString(normalized.type) === "image") {
    const source = readString(normalized.data);
    if (source) {
      normalized.data = resolveBackendAssetUrl(source);
    }
  }

  return normalized;
}

function jsonToHtml(
  items: JsonToHtmlItem[],
  width: number,
  height: number,
  fonts: unknown = {},
  background = "#FFFFFF"
): string {
  const records = items.map(readRecord);
  const chartScripts = records.some(hasChartItem) ? renderChartScripts() : "";
  const fontAssetTags = renderFontAssetTags(fonts);
  const bg = escapeCssColor(background);
  const slideRoot = renderSlideRoot(records, width, height, bg);

  return `<!doctype html>
<html><head><meta charset="utf-8">${fontAssetTags}<style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:${bg}}
body{font-family:Arial,Helvetica,sans-serif}
*,*::before,*::after{box-sizing:border-box}
</style></head><body>${slideRoot}${chartScripts}</body></html>`;
}

function jsonToHtmlFragment(
  items: JsonToHtmlItem[],
  width: number,
  height: number,
  fonts: unknown = {},
  background = "#FFFFFF"
): string {
  const records = items.map(readRecord);
  const bg = escapeCssColor(background);

  return `${renderFontAssetTags(fonts)}${renderSlideRoot(
    records,
    width,
    height,
    bg
  )}`;
}

function renderSlideRoot(
  records: JsonRecord[],
  width: number,
  height: number,
  background: string
): string {
  const content = records.map((item) => renderItem(item, "absolute")).join("");

  return `<div class="relative overflow-hidden" data-template-v2-html-slide="true" style="box-sizing:border-box;position:relative;width:${cssNumber(
    width
  )}px;height:${cssNumber(
    height
  )}px;overflow:hidden;background:${background};font-family:Arial,Helvetica,sans-serif">${content}</div>`;
}
