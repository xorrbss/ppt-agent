import {
  AUTHORED_HYBRID_BROWSER_SOURCE,
  AUTHORED_HYBRID_RESULT_MARKER_ID,
} from "./browser-source.ts";
import {
  AUTHORED_FONT_FALLBACK_POLICY,
  POWERPOINT_GENERIC_FONT_FALLBACK_POLICY,
} from "./font-policy.ts";
import type {
  AuthoredHybridExpectedPromotedElement,
  BrowserAuthoredHybridObservation,
} from "./observation.ts";

const INSTRUMENTATION_STYLE = `
<style id="__presenton_authored_hybrid_stability_v1__">
  *, *::before, *::after {
    animation-play-state: paused !important;
    caret-color: transparent !important;
    transition: none !important;
  }
  ::-webkit-scrollbar { display: none !important; }
</style>`;

export type AuthoredHybridFontLayoutMode = "source" | "powerpoint";

interface InstrumentAuthoredHtmlOptions {
  baseUrl?: string;
  promotedElements?: readonly AuthoredHybridExpectedPromotedElement[];
  /**
   * `source` (the default) preserves the authored/collected face for fidelity
   * rendering. `powerpoint` measures editable text with the local typeface that
   * will be serialized to OOXML.
   */
  fontLayoutMode?: AuthoredHybridFontLayoutMode;
}

const POWERPOINT_FONT_LAYOUT_SCRIPT_ID =
  "__presenton_powerpoint_font_layout_v1__";

function powerPointFontLayoutScript(): string {
  const rules = JSON.stringify([
    ...AUTHORED_FONT_FALLBACK_POLICY,
    ...POWERPOINT_GENERIC_FONT_FALLBACK_POLICY,
  ]).replaceAll("<", "\\u003c");
  return `<script id="${POWERPOINT_FONT_LAYOUT_SCRIPT_ID}">(function(){
    var rules=${rules};
    var policy=new Map(rules.map(function(rule){return [String(rule.authored).toLowerCase(),String(rule.powerpoint)];}));
    var generic=new Set(["serif","sans-serif","monospace","cursive","fantasy","system-ui"]);
    function families(value){return String(value||"").split(",").map(function(family){return family.trim().replace(/^['"]|['"]$/g,"");}).filter(Boolean);}
    function expanded(input){
      var result=[];var seen=new Set();
      function append(family){var normalized=String(family||"").trim().replace(/^['"]|['"]$/g,"");var key=normalized.toLowerCase();if(!normalized||seen.has(key))return;seen.add(key);result.push(normalized);}
      input.forEach(function(family){append(policy.get(String(family||"").trim().replace(/^['"]|['"]$/g,"").toLowerCase()));append(family);});
      return result;
    }
    function css(input){return input.map(function(family){return generic.has(family.toLowerCase())?family:'"'+family.replace(/\\\\/g,"\\\\\\\\").replace(/"/g,'\\\\"')+'"';}).join(", ");}
    [document.documentElement,document.body].concat(Array.from(document.body.querySelectorAll("*"))).forEach(function(element){
      if(!element||["SCRIPT","STYLE","TEXTAREA","NOSCRIPT","TEMPLATE"].includes(element.tagName))return;
      var authored=families(getComputedStyle(element).fontFamily||"sans-serif");
      var mapped=expanded(authored);
      if(mapped.length!==authored.length||mapped.some(function(family,index){return family!==authored[index];})){element.style.setProperty("font-family",css(mapped),"important");}
    });
  })();</script>`;
}

function insertBeforeClosingTag(
  html: string,
  closingTag: string,
  content: string
): string {
  const index = html.toLowerCase().lastIndexOf(closingTag.toLowerCase());
  if (index < 0) return `${html}${content}`;
  return `${html.slice(0, index)}${content}${html.slice(index)}`;
}

function escapeHtmlAttribute(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

export function normalizeAuthoredHybridBaseUrl(
  baseUrl?: string
): string | undefined {
  if (!baseUrl?.trim()) return undefined;
  const parsed = new URL(baseUrl.trim());
  if (!new Set(["http:", "https:", "file:"]).has(parsed.protocol)) {
    throw new Error(`Unsupported authored hybrid base URL protocol: ${parsed.protocol}`);
  }
  return parsed.href;
}

export function instrumentAuthoredHtml(
  html: string,
  options: InstrumentAuthoredHtmlOptions = {}
): string {
  if (!html.trim()) throw new Error("Authored hybrid input HTML must not be empty.");

  const baseUrl = normalizeAuthoredHybridBaseUrl(options.baseUrl);
  const promotedElements = [...(options.promotedElements ?? [])];
  const config = JSON.stringify({
    promotedElements,
    fontFallbackPolicy: AUTHORED_FONT_FALLBACK_POLICY,
    ...(options.fontLayoutMode
      ? { fontLayoutMode: options.fontLayoutMode }
      : {}),
  }).replaceAll("<", "\\u003c");
  const baseInjection = baseUrl
    ? `<base href="${escapeHtmlAttribute(baseUrl)}">`
    : "";
  const layoutInjection =
    options.fontLayoutMode === "powerpoint"
      ? powerPointFontLayoutScript()
      : "";
  const bodyInjection = `<script>window.__PRESENTON_AUTHORED_HYBRID_CONFIG__=${config};</script>${layoutInjection}<script>${AUTHORED_HYBRID_BROWSER_SOURCE}</script>`;

  // The input is copied to a temporary file. Neutralising a CSP meta tag in
  // that copy is necessary for the first-party inline analyser to execute;
  // the persisted authored HTML is never mutated.
  let result = html.replace(
    /<meta\b(?=[^>]*http-equiv\s*=\s*["']?content-security-policy["']?)[^>]*>/gi,
    ""
  );
  if (baseUrl) {
    // A document uses only its first <base>. Replace authored base tags so the
    // caller-supplied asset context is unambiguous for analysis and backplate.
    result = result.replace(/<base\b[^>]*>/gi, "");
  }
  const headOpeningPattern = /<head(?:\s[^>]*)?>/i;
  if (headOpeningPattern.test(result)) {
    // Relative assets are resolved while the parser encounters them, so the
    // caller-controlled base must be the first child of an existing head.
    result = result.replace(
      headOpeningPattern,
      (opening) => `${opening}${baseInjection}`
    );
    result = /<\/head\s*>/i.test(result)
      ? insertBeforeClosingTag(result, "</head>", INSTRUMENTATION_STYLE)
      : `${result}${INSTRUMENTATION_STYLE}`;
  } else if (/<html(?:\s[^>]*)?>/i.test(result)) {
    result = result.replace(
      /<html(?:\s[^>]*)?>/i,
      (opening) =>
        `${opening}<head>${baseInjection}${INSTRUMENTATION_STYLE}</head>`
    );
  } else {
    result = `<head>${baseInjection}${INSTRUMENTATION_STYLE}</head>${result}`;
  }

  if (/<\/body\s*>/i.test(result)) {
    return insertBeforeClosingTag(result, "</body>", bodyInjection);
  }
  return `${result}${bodyInjection}`;
}

interface BrowserResultEnvelope {
  ok: boolean;
  value?: BrowserAuthoredHybridObservation;
  error?: string;
}

export function parseAuthoredHybridDomDump(
  serializedDom: string
): BrowserAuthoredHybridObservation {
  const markerPattern = new RegExp(
    `<script\\b[^>]*\\bid\\s*=\\s*["']${AUTHORED_HYBRID_RESULT_MARKER_ID}["'][^>]*>`,
    "i"
  );
  const markerMatch = markerPattern.exec(serializedDom);
  if (!markerMatch) {
    throw new Error(
      "Chrome completed without the authored hybrid result marker. The page may have blocked instrumentation or exceeded its readiness budget."
    );
  }

  const openingStart = markerMatch.index;
  const openingEnd = markerMatch.index + markerMatch[0].length - 1;
  const closingStart = serializedDom.indexOf("</script>", openingEnd);
  if (openingStart < 0 || openingEnd < 0 || closingStart < 0) {
    throw new Error("Authored hybrid result marker was malformed in Chrome output.");
  }

  const encoded = serializedDom.slice(openingEnd + 1, closingStart).trim();
  let envelope: BrowserResultEnvelope;
  try {
    envelope = JSON.parse(Buffer.from(encoded, "base64").toString("utf8")) as BrowserResultEnvelope;
  } catch (error) {
    throw new Error("Authored hybrid result marker was not valid base64 JSON.", {
      cause: error,
    });
  }

  if (!envelope.ok || !envelope.value) {
    throw new Error(
      `Authored hybrid browser extraction failed: ${envelope.error ?? "unknown browser error"}`
    );
  }
  return envelope.value;
}
