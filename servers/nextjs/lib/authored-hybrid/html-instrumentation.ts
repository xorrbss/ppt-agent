import {
  AUTHORED_HYBRID_BROWSER_SOURCE,
  AUTHORED_HYBRID_RESULT_MARKER_ID,
} from "./browser-source.ts";
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

interface InstrumentAuthoredHtmlOptions {
  baseUrl?: string;
  promotedElements?: readonly AuthoredHybridExpectedPromotedElement[];
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
  const config = JSON.stringify({ promotedElements }).replaceAll("<", "\\u003c");
  const baseInjection = baseUrl
    ? `<base href="${escapeHtmlAttribute(baseUrl)}">`
    : "";
  const bodyInjection = `<script>window.__PRESENTON_AUTHORED_HYBRID_CONFIG__=${config};</script><script>${AUTHORED_HYBRID_BROWSER_SOURCE}</script>`;

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
