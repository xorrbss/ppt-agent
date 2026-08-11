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
  const resourceContract = renderResourceContract(records, fonts);
  const readinessScript = renderReadinessScript();

  return `<!doctype html>
<html><head><meta charset="utf-8">${fontAssetTags}<style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:${bg}}
body{font-family:Arial,Helvetica,sans-serif}
*,*::before,*::after{box-sizing:border-box}
</style></head><body>${slideRoot}${chartScripts}${resourceContract}${readinessScript}</body></html>`;
}

type ResourcePolicy = "required";

interface RenderResourceContract {
  images: Array<{ source: string; policy: ResourcePolicy }>;
  fonts: Array<{ family: string; source: string; policy: ResourcePolicy }>;
}

/**
 * The JSON accepted by this renderer is still an upstream/wire-shaped value.
 * Neither a filename nor an unvalidated `resourcePolicy` field proves that a
 * resource is expendable, so the lowering is deliberately fail-closed. A
 * future canonical placeholder node may emit an optional proof only after that
 * identity is validated by the AST compiler; raw records cannot opt out.
 */
function resourcePolicy(): ResourcePolicy {
  return "required";
}

function renderResourceContract(records: JsonRecord[], fonts: unknown): string {
  const contract: RenderResourceContract = {
    images: collectImageResources(records),
    fonts: collectFontResources(fonts),
  };
  const json = JSON.stringify(contract).replaceAll("<", "\\u003c");
  return `<script type="application/json" data-presenton-template-v2-resource-contract="true">${json}</script>`;
}

function collectImageResources(records: JsonRecord[]): RenderResourceContract["images"] {
  const resources = new Map<string, ResourcePolicy>();
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    const record = readRecord(value);
    if (!Object.keys(record).length) return;
    if (readString(record.type) === "image") {
      const source = readString(record.data);
      if (source) {
        const policy = resourcePolicy();
        if (resources.get(source) !== "required") resources.set(source, policy);
      }
    }
    Object.values(record).forEach(visit);
  };
  records.forEach(visit);
  return [...resources].map(([source, policy]) => ({ source, policy }));
}

function collectFontResources(fonts: unknown): RenderResourceContract["fonts"] {
  const resources = new Map<string, { family: string; source: string; policy: ResourcePolicy }>();
  const visit = (familyHint: string | undefined, value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach((item) => visit(familyHint, item));
      return;
    }
    const direct = readString(value);
    if (direct && familyHint) {
      resources.set(`${familyHint}\0${direct}`, {
        family: familyHint,
        source: resolveBackendAssetUrl(direct),
        policy: "required",
      });
      return;
    }
    const record = readRecord(value);
    if (!Object.keys(record).length) return;
    const family =
      readString(record.family ?? record.name ?? record.fontFamily ?? record.font_family) ??
      familyHint;
    const source = readString(
      record.url ?? record.src ?? record.href ?? record.data ?? record.source
    );
    if (family && source) {
      resources.set(`${family}\0${source}`, {
        family,
        source: resolveBackendAssetUrl(source),
        policy: "required",
      });
      return;
    }
    Object.entries(record).forEach(([key, item]) => {
      if (!new Set(["css", "font_css", "fonts"]).has(key)) visit(key, item);
    });
  };
  const fontRecord = readRecord(fonts);
  const faces = readArray(fontRecord.fonts).length ? fontRecord.fonts : fonts;
  if (Array.isArray(faces)) faces.forEach((value) => visit(undefined, value));
  else Object.entries(readRecord(faces)).forEach(([family, value]) => visit(family, value));
  return [...resources.values()];
}

/**
 * Expose a deterministic browser-paint boundary for screenshot/export callers.
 *
 * A completed navigation is not sufficient for image/font backed slides: the
 * browser may not have committed those decoded resources to the compositor yet.
 * Waiting for every image, the font set, and two animation frames gives all
 * renderers the same package-agnostic readiness contract. The script is owned by
 * the renderer; template content is still treated as data and cannot add code.
 */
function renderReadinessScript(): string {
  return `<script data-presenton-template-v2-readiness="true">(()=>{const root=document.documentElement,node=document.querySelector('script[data-presenton-template-v2-resource-contract]'),contract=node?JSON.parse(node.textContent||'{}'):{},failures=[],chartTimeoutMs=10000,href=source=>{try{return new URL(source,document.baseURI).href}catch{return 'invalid:'+source}};root.dataset.templateV2RenderState='pending';const imageSources=new Set([...(contract.images||[]).map(x=>href(x.source)),...Array.from(document.images,x=>x.currentSrc||x.src)]),loadImage=source=>new Promise(resolve=>{const image=new Image;let settled=false;const done=ok=>{if(settled)return;settled=true;if(!ok)failures.push({kind:'image',source});resolve()};image.onload=()=>done(image.naturalWidth>0);image.onerror=()=>done(false);image.src=source;if(image.complete)done(image.naturalWidth>0)}),loadFonts=async()=>{for(const link of document.querySelectorAll('link[rel="stylesheet"]')){const timing=performance.getEntriesByName(link.href).at(-1),failed=!link.sheet||(timing&&'responseStatus'in timing&&timing.responseStatus>=400);if(failed)failures.push({kind:'font-stylesheet',source:link.href})}if(!document.fonts)return;await document.fonts.ready.catch(()=>{});await Promise.all(Array.from(document.fonts,async face=>{try{await face.load()}catch{failures.push({kind:'font',family:face.family})}}))},loadCharts=()=>new Promise(resolve=>{if(!document.querySelector('canvas[data-presenton-chart]')){resolve();return}const deadline=Date.now()+chartTimeoutMs,check=()=>{const state=window.__PRESENTON_JSON_CHARTS__;if(state&&state.status==='ready'){resolve();return}if(state&&state.status==='error'){failures.push({kind:'chart',code:'chart-render-failed'});resolve();return}if(Date.now()>=deadline){failures.push({kind:'chart',code:'chart-render-timeout'});resolve();return}setTimeout(check,25)};check()}),publishError=(code,errorFailures)=>{root.dataset.templateV2RenderState='error';root.dataset.templateV2ResourceError='true';root.dataset.templateV2ResourceErrorCode=code;window.__presentonTemplateV2ResourceErrors=errorFailures;window.dispatchEvent(new Event('template-v2-render-error'))};Promise.all([...Array.from(imageSources,loadImage),loadFonts(),loadCharts()]).then(()=>{if(failures.length){const chartFailure=failures.find(item=>item.kind==='chart');publishError(chartFailure&&chartFailure.code?chartFailure.code:'required-resource-failed',failures);return}return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>setTimeout(resolve,100)))).then(()=>{root.dataset.templateV2RenderState='ready';root.dataset.templateV2RenderReady='true';window.dispatchEvent(new Event('template-v2-render-ready'))})}).catch(()=>publishError('readiness-exception',[{kind:'readiness',code:'readiness-exception'}]))})()</script>`;
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
