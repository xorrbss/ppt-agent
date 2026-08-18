// Only the untrusted authored document passes through here. Collector-built
// @font-face CSS is injected into a separate document after this preflight
// succeeds, so the preflighted HTML never carries inlined font payloads.
import { createHash } from "node:crypto";
import {
  AUTHORED_RENDER_LIMITS,
  validateHybridDataFontUrl,
  validateHybridDataImageUrl,
} from "./security-data.ts";

export {
  AUTHORED_RENDER_LIMITS,
  readBoundedResponseText,
  validateHybridDataFontUrl,
  validateHybridDataImageUrl,
  type HybridDataFontResult,
  type HybridDataImageResult,
} from "./security-data.ts";

export interface HybridHtmlPreflightResult {
  ok: boolean;
  reason?: string;
}

export interface AuthoredHtmlResourceInspection {
  imageDigests: readonly string[];
  fontDigests: readonly string[];
  aggregateImageBytes: number;
  aggregateFontBytes: number;
  nodeCount: number;
  maxDepth: number;
}

const ALLOWED_HTML_TAGS = new Set([
  "html", "head", "title", "meta", "body", "main", "section", "article",
  "header", "footer", "nav", "aside", "div", "span", "p", "h1", "h2",
  "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd", "table",
  "thead", "tbody", "tfoot", "tr", "th", "td", "colgroup", "col", "strong",
  "b", "em", "i", "u", "s", "small", "mark", "sub", "sup", "br", "hr",
  "img", "figure", "figcaption", "blockquote", "q", "code", "pre", "style",
  "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
  "polygon", "text", "tspan", "defs", "lineargradient", "radialgradient",
  "stop", "clippath", "mask", "filter", "fegaussianblur", "feoffset",
  "fecolormatrix", "feblend", "fecomposite", "femerge", "femergenode", "marker",
  "fedropshadow", "symbol", "use",
]);

const GLOBAL_ATTRIBUTES = new Set([
  "id", "class", "style", "role", "lang", "dir", "title", "tabindex",
  "width", "height", "viewbox", "preserveaspectratio", "xmlns", "fill", "stroke",
  "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity",
  "fill-opacity", "stroke-opacity", "transform", "d", "x", "y", "x1", "x2", "y1",
  "y2", "cx", "cy", "r", "rx", "ry", "points", "dx", "dy", "text-anchor",
  "dominant-baseline", "font-size", "font-family", "font-weight", "offset",
  "stop-color", "stop-opacity", "gradientunits", "gradienttransform", "spreadmethod",
  "filter", "clip-path", "mask", "marker-start", "marker-mid", "marker-end",
  "pathlength", "vector-effect", "color", "colspan", "rowspan", "scope", "alt",
  "src", "href", "xlink:href", "charset", "content", "name", "type",
  "markerwidth", "markerheight", "refx", "refy", "orient", "stddeviation",
  "flood-color", "flood-opacity",
]);

const VOID_TAGS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
const ATTRIBUTE_PATTERN = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
const ALLOWED_CSS_PROPERTIES = new Set([
  "align-content", "align-items", "align-self", "all", "animation", "animation-delay",
  "animation-direction", "animation-duration", "animation-fill-mode", "animation-iteration-count",
  "animation-name", "animation-play-state", "animation-timing-function", "appearance",
  "aspect-ratio", "backdrop-filter", "backface-visibility", "background", "background-attachment",
  "background-blend-mode", "background-clip", "background-color", "background-image",
  "background-origin", "background-position", "background-repeat", "background-size", "border",
  "border-block", "border-block-color", "border-block-end", "border-block-start", "border-bottom",
  "border-bottom-color", "border-bottom-left-radius", "border-bottom-right-radius", "border-bottom-style",
  "border-bottom-width", "border-collapse", "border-color", "border-image", "border-inline",
  "border-left", "border-left-color", "border-left-style", "border-left-width", "border-radius",
  "border-right", "border-right-color", "border-right-style", "border-right-width", "border-spacing",
  "border-style", "border-top", "border-top-color", "border-top-left-radius", "border-top-right-radius",
  "border-top-style", "border-top-width", "border-width", "bottom", "box-decoration-break", "box-shadow",
  "box-sizing", "break-after", "break-before", "break-inside", "caret-color", "clear", "clip-path",
  "color", "column-count", "column-gap", "column-width", "columns", "content", "cursor", "direction",
  "display", "fill", "fill-opacity", "filter", "flex", "flex-basis", "flex-direction", "flex-flow", "flex-grow", "flex-shrink",
  "flex-wrap", "float", "font", "font-family", "font-feature-settings", "font-kerning", "font-optical-sizing",
  "font-display", "font-size", "font-stretch", "font-style", "font-variant", "font-variant-caps",
  "font-variant-numeric", "font-variation-settings",
  "font-weight", "gap", "grid", "grid-area", "grid-auto-columns", "grid-auto-flow", "grid-auto-rows",
  "grid-column", "grid-column-end", "grid-column-gap", "grid-column-start", "grid-gap", "grid-row",
  "grid-row-end", "grid-row-gap", "grid-row-start", "grid-template", "grid-template-areas",
  "grid-template-columns", "grid-template-rows", "height", "hyphens", "inset", "inset-block",
  "inset-inline", "isolation", "justify-content", "justify-items", "justify-self", "left", "letter-spacing",
  "line-break", "line-height", "list-style", "list-style-image", "list-style-position", "list-style-type",
  "margin", "margin-block", "margin-block-end", "margin-block-start", "margin-bottom", "margin-inline",
  "margin-inline-end", "margin-inline-start", "margin-left", "margin-right", "margin-top", "mask", "mask-image", "mask-position", "mask-repeat", "mask-size", "max-height",
  "marker-end", "marker-mid", "marker-start", "max-width", "min-height", "min-width", "mix-blend-mode", "object-fit", "object-position", "opacity",
  "order", "outline", "outline-color", "outline-offset", "outline-style", "outline-width", "overflow",
  "overflow-wrap", "overflow-x", "overflow-y", "padding", "padding-block", "padding-block-end",
  "padding-block-start", "padding-bottom", "padding-inline", "padding-inline-end", "padding-inline-start",
  "padding-left", "padding-right", "padding-top", "perspective", "perspective-origin", "place-content",
  "place-items", "place-self", "pointer-events", "position", "resize", "right", "rotate", "row-gap", "scale",
  "shape-outside", "table-layout", "text-align", "text-decoration", "text-decoration-color",
  "text-decoration-line", "text-decoration-style", "text-indent", "text-overflow", "text-shadow",
  "stroke", "stroke-dasharray", "stroke-dashoffset", "stroke-linecap", "stroke-linejoin", "stroke-opacity", "stroke-width",
  "text-transform", "text-underline-offset", "top", "transform", "transform-origin", "transform-style",
  "transition", "transition-delay", "transition-duration", "transition-property", "transition-timing-function",
  "translate", "unicode-bidi", "user-select", "vertical-align", "visibility", "white-space", "widows",
  "width", "word-break", "word-spacing", "word-wrap", "writing-mode", "z-index",
]);

function validateCssAllowlist(html: string, allowCollectorFonts: boolean): HybridHtmlPreflightResult {
  const regions = [
    ...[...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map((match) => match[1]),
    ...[...html.matchAll(/\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/gi)].map((match) => match[1] ?? match[2] ?? ""),
  ];
  for (const css of regions) {
    const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
    for (const atRule of withoutComments.matchAll(/@([a-z-]+)/gi)) {
      const name = atRule[1].toLowerCase();
      if (!(name === "media" || name === "supports" || name === "keyframes" || (allowCollectorFonts && name === "font-face"))) {
        return { ok: false, reason: "css-at-rule-not-allowlisted" };
      }
    }
    for (const declaration of withoutComments.matchAll(/(?:^|[;{])\s*([a-z-]+|--[a-z0-9_-]+)\s*:/gi)) {
      const property = declaration[1].toLowerCase();
      if (!property.startsWith("--") && !ALLOWED_CSS_PROPERTIES.has(property) && !(allowCollectorFonts && property === "src")) {
        return { ok: false, reason: `css-property-not-allowlisted:${property}` };
      }
    }
  }
  return { ok: true };
}

function decodeHtmlEntities(value: string): string {
  const codePoint = (digits: string, radix: number) => {
    const parsed = Number.parseInt(digits, radix);
    return Number.isSafeInteger(parsed) && parsed >= 0 && parsed <= 0x10ffff
      ? String.fromCodePoint(parsed)
      : "\ufffd";
  };
  return value
    .replace(/&#x([0-9a-f]+);?/gi, (_, digits: string) => codePoint(digits, 16))
    .replace(/&#([0-9]+);?/g, (_, digits: string) => codePoint(digits, 10))
    .replace(/&colon;/gi, ":")
    .replace(/&tab;/gi, "\t")
    .replace(/&newline;/gi, "\n")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&amp;/gi, "&");
}

function imageDimensions(
  mime: "png" | "jpeg" | "webp",
  bytes: Buffer
): { width: number; height: number } | undefined {
  if (mime === "png" && bytes.length >= 24) {
    return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
  }
  if (mime === "jpeg") {
    let offset = 2;
    while (offset + 9 < bytes.length) {
      if (bytes[offset] !== 0xff) { offset += 1; continue; }
      const marker = bytes[offset + 1];
      if (marker === 0xd8 || marker === 0xd9) { offset += 2; continue; }
      if (offset + 4 > bytes.length) break;
      const length = bytes.readUInt16BE(offset + 2);
      if (length < 2 || offset + 2 + length > bytes.length) break;
      if (new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]).has(marker)) {
        return { height: bytes.readUInt16BE(offset + 5), width: bytes.readUInt16BE(offset + 7) };
      }
      offset += 2 + length;
    }
  }
  if (mime === "webp" && bytes.length >= 30) {
    const kind = bytes.toString("ascii", 12, 16);
    if (kind === "VP8X") {
      return {
        width: 1 + bytes.readUIntLE(24, 3),
        height: 1 + bytes.readUIntLE(27, 3),
      };
    }
    if (kind === "VP8 " && bytes.length >= 30) {
      return {
        width: bytes.readUInt16LE(26) & 0x3fff,
        height: bytes.readUInt16LE(28) & 0x3fff,
      };
    }
    if (kind === "VP8L" && bytes.length >= 25) {
      const bits = bytes.readUInt32LE(21);
      return { width: 1 + (bits & 0x3fff), height: 1 + ((bits >>> 14) & 0x3fff) };
    }
  }
  return undefined;
}

function validatePixelLimit(mime: "png" | "jpeg" | "webp", bytes: Buffer): boolean {
  const dimensions = imageDimensions(mime, bytes);
  return Boolean(
    dimensions &&
      dimensions.width > 0 &&
      dimensions.height > 0 &&
      dimensions.width <= AUTHORED_RENDER_LIMITS.maxImageDimension &&
      dimensions.height <= AUTHORED_RENDER_LIMITS.maxImageDimension &&
      dimensions.width * dimensions.height <= AUTHORED_RENDER_LIMITS.maxImagePixels
  );
}

function inspectStructure(html: string): HybridHtmlPreflightResult & { nodeCount: number; maxDepth: number } {
  let nodeCount = 0;
  let maxDepth = 0;
  const stack: string[] = [];
  const tokenPattern = /<!--[\s\S]*?-->|<![^>]*>|<\/?[A-Za-z][^>]*>|[^<]+|</g;
  for (const match of html.matchAll(tokenPattern)) {
    const token = match[0];
    if (token.startsWith("<!--") || /^<!/i.test(token)) continue;
    nodeCount += 1;
    if (nodeCount > AUTHORED_RENDER_LIMITS.maxNodes) {
      return { ok: false, reason: "dom-node-limit", nodeCount, maxDepth };
    }
    if (!token.startsWith("<") || token === "<") {
      if (Buffer.byteLength(token, "utf8") > AUTHORED_RENDER_LIMITS.maxTextNodeBytes) {
        return { ok: false, reason: "text-node-limit", nodeCount, maxDepth };
      }
      continue;
    }
    if (token.startsWith("</")) {
      const tag = token.slice(2, -1).trim().toLowerCase();
      if (stack.at(-1) !== tag) return { ok: false, reason: "malformed-html", nodeCount, maxDepth };
      stack.pop();
      continue;
    }
    const tagMatch = token.match(/^<\s*([^\s/>]+)([\s\S]*?)\/?\s*>$/);
    if (!tagMatch) return { ok: false, reason: "malformed-html", nodeCount, maxDepth };
    const tag = tagMatch[1].toLowerCase();
    if (!ALLOWED_HTML_TAGS.has(tag)) return { ok: false, reason: `tag-not-allowlisted:${tag}`, nodeCount, maxDepth };
    const attributes = [...tagMatch[2].matchAll(ATTRIBUTE_PATTERN)];
    if (attributes.length > AUTHORED_RENDER_LIMITS.maxAttributesPerElement) {
      return { ok: false, reason: "attribute-limit", nodeCount, maxDepth };
    }
    const seen = new Set<string>();
    for (const attribute of attributes) {
      const name = attribute[1].toLowerCase();
      if (seen.has(name)) return { ok: false, reason: "duplicate-attribute", nodeCount, maxDepth };
      seen.add(name);
      if (/^on/i.test(name) || name === "srcdoc") return { ok: false, reason: "active-or-external-html", nodeCount, maxDepth };
      if (!(GLOBAL_ATTRIBUTES.has(name) || /^aria-[a-z0-9_-]+$/.test(name) || /^data-[a-z0-9_.:-]+$/.test(name))) {
        return { ok: false, reason: `attribute-not-allowlisted:${name}`, nodeCount, maxDepth };
      }
      const value = decodeHtmlEntities(attribute[2] ?? attribute[3] ?? attribute[4] ?? "");
      if (/(?:^|[\\/])\.\.(?:[\\/]|$)|%2e%2e(?:%2f|%5c)/i.test(value)) {
        return { ok: false, reason: "path-traversal", nodeCount, maxDepth };
      }
      if (value.includes("__METADATA_ENDPOINT_DO_NOT_RESOLVE__")) {
        return { ok: false, reason: "reserved-metadata-target", nodeCount, maxDepth };
      }
      if (/\u0000|[\r\n]/.test(value) && ["src", "href", "xlink:href"].includes(name)) {
        return { ok: false, reason: "unsafe-url-attribute", nodeCount, maxDepth };
      }
      if (tag === "img" && (name === "width" || name === "height")) {
        const dimension = Number(value);
        if (!Number.isFinite(dimension) || dimension <= 0 || dimension > AUTHORED_RENDER_LIMITS.maxImageDimension) {
          return { ok: false, reason: "image-dimension-limit", nodeCount, maxDepth };
        }
      }
    }
    if (!token.endsWith("/>") && !VOID_TAGS.has(tag)) {
      stack.push(tag);
      maxDepth = Math.max(maxDepth, stack.length);
      if (stack.length > AUTHORED_RENDER_LIMITS.maxDomDepth) {
        return { ok: false, reason: "dom-depth-limit", nodeCount, maxDepth };
      }
    }
  }
  if (stack.length > 0) return { ok: false, reason: "malformed-html", nodeCount, maxDepth };
  return { ok: true, nodeCount, maxDepth };
}

/** Concatenate every CSS region (inline `<style>` blocks + `style=` attribute
 * values) so CSS-only guards run against style content instead of the whole
 * document — plain-text backslashes ("C:\\Users") or the word "expression"
 * in prose must not trip a CSS escape/expression check. */
function extractCssRegions(html: string): string {
  const regions: string[] = [];
  for (const match of html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
    regions.push(match[1]);
  }
  for (const match of html.matchAll(
    /\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/gi
  )) {
    regions.push(match[1] ?? match[2] ?? "");
  }
  return regions.join("\n");
}

/**
 * The isolated Chrome worker accepts only the static, self-contained subset
 * produced by the authored pipeline. Anything active or network-bearing is
 * rejected before the CDP pipe receives a document.
 */
export function preflightAuthoredHtmlForHybrid(
  html: string
): HybridHtmlPreflightResult {
  if (!html.trim()) return { ok: false, reason: "empty-html" };
  if (html.includes("__METADATA_ENDPOINT_DO_NOT_RESOLVE__")) {
    return { ok: false, reason: "reserved-metadata-target" };
  }
  if (Buffer.byteLength(html, "utf8") > AUTHORED_RENDER_LIMITS.maxHtmlBytes) {
    return { ok: false, reason: "html-too-large" };
  }

  // Structural markup that is unsafe wherever it appears.
  const forbiddenMarkup = [
    /<\s*script\b/i,
    /<\s*(?:iframe|frame|frameset|object|embed|applet)\b/i,
    /<\s*(?:form|input|button|textarea|select)\b/i,
    /<\s*link\b/i,
    /<\s*base\b/i,
    /<\s*foreignObject\b/i,
    /<\s*meta\b[^>]*http-equiv\s*=\s*["']?refresh\b/i,
    /(?:\s|\/)(?:on[a-z0-9_-]+|srcdoc)\s*=/i,
    /\b(?:srcset|imagesrcset|ping)\s*=/i,
  ];
  if (forbiddenMarkup.some((pattern) => pattern.test(html))) {
    return { ok: false, reason: "active-or-external-html" };
  }

  const structure = inspectStructure(html);
  if (!structure.ok) return { ok: false, reason: structure.reason };

  // Dangerous URL protocols only matter inside a URL context — an attribute
  // value (after `=` or a quote) or a CSS url(). Anchoring to that context
  // avoids rejecting ordinary prose like "Profile:" or "Dockerfile:".
  if (/["'=(]\s*(?:javascript|vbscript|file|blob|ftp):/i.test(html)) {
    return { ok: false, reason: "active-or-external-html" };
  }

  // CSS-only constructs (escape obfuscation, remote/vector fetches, IE
  // expressions) are checked against style regions only.
  const css = extractCssRegions(html);
  const forbiddenCss = [
    /@import\b/i,
    /\b(?:-webkit-)?image-set\s*\(/i,
    /\\(?:[0-9a-f]{1,6}[ \t\r\n\f]?|[^\r\n\f0-9a-f])/i,
    /expression\s*\(/i,
  ];
  if (forbiddenCss.some((pattern) => pattern.test(css))) {
    return { ok: false, reason: "active-or-external-html" };
  }

  // Every CSS url() must be an embedded safe raster. A font program is never
  // accepted from the untrusted document: only the server-side collector may
  // introduce @font-face payloads, and it does so in the separate document
  // that bypasses this preflight. Static SVG markup itself remains eligible
  // for the backplate, but SVG data URLs are intentionally not.
  const cssUrlPattern = /url\(\s*(["']?)(.*?)\1\s*\)/gis;
  for (const match of html.matchAll(cssUrlPattern)) {
    const value = match[2].trim();
    // Same-document SVG paint servers and markers (for example
    // marker-end:url(#arrow)) do not load external content.
    if (/^#[A-Za-z_][A-Za-z0-9_.:-]*$/.test(value)) continue;
    const result = validateHybridDataImageUrl(value);
    if (!result.ok) return { ok: false, reason: "unsafe-css-url" };
  }

  const urlAttributePattern =
    /\b(src|href|poster|action|formaction|xlink:href)\s*=\s*(?:(["'])(.*?)\2|([^\s>]+))/gis;
  for (const match of html.matchAll(urlAttributePattern)) {
    const attribute = match[1].toLowerCase();
    const value = (match[3] ?? match[4] ?? "").trim();
    if (attribute.endsWith("href") && value.startsWith("#")) continue;
    if (attribute !== "src") {
      return { ok: false, reason: "unsafe-url-attribute" };
    }
    const result = validateHybridDataImageUrl(value);
    if (!result.ok) return { ok: false, reason: "unsafe-image-source" };
  }

  const cssPolicy = validateCssAllowlist(html, false);
  if (!cssPolicy.ok) return cssPolicy;

  const resources = inspectAuthoredHtmlResources(html, false);
  return resources.ok ? { ok: true } : resources;
}

/**
 * Internal collector boundary. Unlike authored preflight, this permits only
 * valid, embedded font programs and remains self-contained. It never permits
 * script, network, blob, file, or relative URL content.
 */
export function preflightTrustedSelfContainedAuthoredHtml(
  html: string
): HybridHtmlPreflightResult {
  if (!html.trim()) return { ok: false, reason: "empty-html" };
  if (Buffer.byteLength(html, "utf8") > AUTHORED_RENDER_LIMITS.maxHtmlBytes) {
    return { ok: false, reason: "html-too-large" };
  }
  const withoutFontFaces = html.replace(/@font-face\s*\{[^{}]*\}/gi, "");
  const authored = preflightAuthoredHtmlForHybrid(withoutFontFaces);
  if (!authored.ok) return authored;
  const structure = inspectStructure(html);
  if (!structure.ok) return { ok: false, reason: structure.reason };
  const cssPolicy = validateCssAllowlist(html, true);
  if (!cssPolicy.ok) return cssPolicy;
  const resources = inspectAuthoredHtmlResources(html, true);
  return resources.ok ? { ok: true } : resources;
}

export function inspectAuthoredHtmlResources(
  html: string,
  allowCollectorFonts = false
): (HybridHtmlPreflightResult & Partial<AuthoredHtmlResourceInspection>) {
  const structure = inspectStructure(html);
  if (!structure.ok) return structure;
  const imageDigests: string[] = [];
  const fontDigests: string[] = [];
  let aggregateImageBytes = 0;
  let aggregateFontBytes = 0;

  const dataUrlPattern = /data:(image\/(?:png|jpeg|webp)|font\/(?:woff2|woff|otf|ttf));base64,[A-Za-z0-9+/]*={0,2}/gi;
  for (const match of html.matchAll(dataUrlPattern)) {
    const source = match[0];
    if (/^data:image\//i.test(source)) {
      const image = validateHybridDataImageUrl(source);
      if (!image.ok) return { ok: false, reason: image.reason };
      if (!validatePixelLimit(image.mime, image.bytes)) {
        return { ok: false, reason: "image-pixel-limit" };
      }
      aggregateImageBytes += image.bytes.length;
      if (aggregateImageBytes > AUTHORED_RENDER_LIMITS.maxAggregateAssetBytes) {
        return { ok: false, reason: "aggregate-asset-limit" };
      }
      imageDigests.push(createHash("sha256").update(image.bytes).digest("hex"));
      continue;
    }
    if (!allowCollectorFonts) return { ok: false, reason: "unsafe-css-url" };
    const font = validateHybridDataFontUrl(source);
    if (!font.ok) return { ok: false, reason: font.reason };
    aggregateFontBytes += font.bytes.length;
    if (aggregateFontBytes > AUTHORED_RENDER_LIMITS.maxAggregateFontBytes) {
      return { ok: false, reason: "aggregate-font-limit" };
    }
    fontDigests.push(createHash("sha256").update(font.bytes).digest("hex"));
  }

  const allDataUrls = [...html.matchAll(/data:[^\s"')>]+/gi)];
  if (allDataUrls.length !== imageDigests.length + fontDigests.length) {
    return { ok: false, reason: "unsupported-data-url" };
  }
  const fontFaces = [...html.matchAll(/@font-face\b/gi)].length;
  if ((!allowCollectorFonts && fontFaces > 0) || fontFaces > AUTHORED_RENDER_LIMITS.maxFontFaces) {
    return { ok: false, reason: "font-face-limit" };
  }
  const fontFamilies = new Set(
    [...html.matchAll(/font-family\s*:\s*([^;}]+)/gi)]
      .flatMap((match) => match[1].split(","))
      .map((family) => family.trim().replace(/^['"]|['"]$/g, "").toLowerCase())
      .filter(Boolean)
  );
  if (fontFamilies.size > AUTHORED_RENDER_LIMITS.maxFontFamilies) {
    return { ok: false, reason: "font-family-limit" };
  }
  return {
    ok: true,
    imageDigests,
    fontDigests,
    aggregateImageBytes,
    aggregateFontBytes,
    nodeCount: structure.nodeCount,
    maxDepth: structure.maxDepth,
  };
}
