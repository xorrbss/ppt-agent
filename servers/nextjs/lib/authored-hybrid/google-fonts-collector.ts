/**
 * Server-only Google Fonts collector for authored-hybrid export.
 *
 * Orchestration only: URL rules live in google-fonts-url-policy, transport and
 * caching in google-fonts-fetch, and HTML/CSS parsing in google-fonts-css.
 * Chrome remains fully offline; all network access happens here, before the
 * untrusted preflight and before Chrome ever sees the document.
 */

import {
  type GoogleFontsCollectorOptions,
  type NormalizedGoogleFontsCollectorOptions,
  fetchValidatedResource,
  normalizeGoogleFontsCollectorOptions,
} from "./google-fonts-fetch.ts";
import {
  type ParsedFontFace,
  collectSourceCodePoints,
  escapeCssString,
  extractGoogleFontsReferences,
  fontFaceCoversSource,
  injectTrustedCss,
  parseFontFaceBody,
} from "./google-fonts-css.ts";

export type { GoogleFontsCollectorOptions };
export {
  validateGoogleFontsCssUrl,
  validateGoogleFontsWoff2Url,
} from "./google-fonts-url-policy.ts";

export type GoogleFontsCollectionStatus =
  | "not-requested"
  | "success"
  | "partial"
  | "unavailable";

export interface GoogleFontsCollectionFailure {
  url: string;
  reason: string;
}

export interface PreparedGoogleFontsHtml {
  /**
   * The untrusted document with only validated Google Fonts link/import
   * references removed. This value, never htmlForChrome, must pass the normal
   * authored-hybrid preflight before the trusted CSS is used.
   */
  htmlForPreflight: string;
  /**
   * htmlForPreflight plus collector-generated, validated data @font-face CSS.
   * It is safe to use only after htmlForPreflight passes the normal preflight.
   */
  htmlForChrome: string;
  status: GoogleFontsCollectionStatus;
  reason?: string;
  usedFallback: boolean;
  requestedStylesheets: number;
  requestedFontFaces: number;
  loadedStylesheets: number;
  fontFiles: number;
  /**
   * Font-family names parsed from collector-validated @font-face rules whose
   * WOFF2 payloads were fetched and verified successfully. Only these names
   * may cross into native OOXML as authored typefaces.
   */
  trustedFamilies: string[];
  failures: GoogleFontsCollectionFailure[];
}

interface TrustedFontFace {
  css: string;
  fontBytes: number;
  family: string;
}

async function buildTrustedFontFace(
  parsed: ParsedFontFace,
  options: NormalizedGoogleFontsCollectorOptions
): Promise<TrustedFontFace> {
  const font = await fetchValidatedResource(parsed.fontUrl, "woff2", options);
  const dataUrl = `data:font/woff2;base64,${font.bytes.toString("base64")}`;
  const descriptors = [
    `font-family:'${escapeCssString(parsed.family)}'`,
    `font-style:${parsed.style}`,
    `font-weight:${parsed.weight}`,
    `font-stretch:${parsed.stretch}`,
    "font-display:block",
    `src:url('${dataUrl}') format('woff2')`,
  ];
  if (parsed.unicodeRange) {
    descriptors.push(`unicode-range:${parsed.unicodeRange}`);
  }
  return {
    css: `@font-face{${descriptors.join(";")}}`,
    fontBytes: font.bytes.length,
    family: parsed.family,
  };
}

async function buildTrustedFontFaces(
  css: string,
  options: NormalizedGoogleFontsCollectorOptions,
  sourceCodePoints: Set<number>
): Promise<TrustedFontFace[]> {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const blocks = [...withoutComments.matchAll(/@font-face\s*\{([^{}]*)\}/gi)];
  const outside = withoutComments.replace(/@font-face\s*\{[^{}]*\}/gi, "").trim();
  if (!blocks.length || outside) {
    throw new Error("google-font-css-contains-unsupported-rules");
  }
  const parsedFaces = blocks
    .map((block) => parseFontFaceBody(block[1]))
    .filter((face) => fontFaceCoversSource(face, sourceCodePoints));
  if (parsedFaces.length > options.maxFontFiles) {
    throw new Error("google-font-css-has-too-many-fonts");
  }

  const trusted: TrustedFontFace[] = [];
  let totalBytes = 0;
  for (const parsed of parsedFaces) {
    const face = await buildTrustedFontFace(parsed, options);
    totalBytes += face.fontBytes;
    if (totalBytes > options.maxTotalFontBytes) {
      throw new Error("google-font-total-size-limit-exceeded");
    }
    trusted.push(face);
  }
  return trusted;
}


function errorReason(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === "AbortError") return "google-font-fetch-timeout";
    return error.message || "google-font-collection-failed";
  }
  return "google-font-collection-failed";
}

/**
 * Server-only Google Fonts collector for authored-hybrid export. Chrome remains
 * fully offline: all network access happens here, before preflight/Chrome.
 */

export class GoogleFontsCollector {
  readonly options: NormalizedGoogleFontsCollectorOptions;

  constructor(options: GoogleFontsCollectorOptions) {
    this.options = normalizeGoogleFontsCollectorOptions(options);
  }

  async prepareHtml(html: string): Promise<PreparedGoogleFontsHtml> {
    const extracted = extractGoogleFontsReferences(html);
    if (
      !extracted.stylesheets.length &&
      !extracted.inlineFontFaceBodies.length
    ) {
      return {
        htmlForPreflight: extracted.htmlForPreflight,
        htmlForChrome: extracted.htmlForPreflight,
        status: "not-requested",
        usedFallback: false,
        requestedStylesheets: 0,
        requestedFontFaces: 0,
        loadedStylesheets: 0,
        fontFiles: 0,
        trustedFamilies: [],
        failures: [],
      };
    }

    if (!this.options.enabled) {
      // Offline deployments turn collection off entirely. The sanitized
      // document is still returned so the deck renders with the local
      // fallback stacks rather than failing the export.
      return {
        htmlForPreflight: extracted.htmlForPreflight,
        htmlForChrome: extracted.htmlForPreflight,
        status: "unavailable",
        reason: "google-font-collection-disabled",
        usedFallback: true,
        requestedStylesheets: extracted.stylesheets.length,
        requestedFontFaces: extracted.inlineFontFaceBodies.length,
        loadedStylesheets: 0,
        fontFiles: 0,
        trustedFamilies: [],
        failures: [],
      };
    }

    const failures: GoogleFontsCollectionFailure[] = [];
    const trustedCss: string[] = [];
    const trustedFamilies = new Set<string>();
    let loadedStylesheets = 0;
    let loadedInlineFontFaces = 0;
    let fontFiles = 0;
    let totalFontBytes = 0;
    const sourceCodePoints = collectSourceCodePoints(
      extracted.htmlForPreflight
    );
    for (const stylesheetUrl of extracted.stylesheets) {
      try {
        const stylesheet = await fetchValidatedResource(
          stylesheetUrl,
          "css",
          this.options
        );
        const faces = await buildTrustedFontFaces(
          stylesheet.bytes.toString("utf8"),
          this.options,
          sourceCodePoints
        );
        const stylesheetFontBytes = faces.reduce(
          (total, face) => total + face.fontBytes,
          0
        );
        if (
          fontFiles + faces.length > this.options.maxFontFiles ||
          totalFontBytes + stylesheetFontBytes >
            this.options.maxTotalFontBytes
        ) {
          throw new Error("google-font-total-size-limit-exceeded");
        }
        trustedCss.push(...faces.map((face) => face.css));
        faces.forEach((face) => trustedFamilies.add(face.family));
        loadedStylesheets += 1;
        fontFiles += faces.length;
        totalFontBytes += stylesheetFontBytes;
      } catch (error) {
        failures.push({
          url: stylesheetUrl,
          reason: errorReason(error),
        });
      }
    }
    for (const body of extracted.inlineFontFaceBodies) {
      let parsed: ParsedFontFace | null = null;
      try {
        parsed = parseFontFaceBody(body);
        const face = await buildTrustedFontFace(parsed, this.options);
        if (
          fontFiles + 1 > this.options.maxFontFiles ||
          totalFontBytes + face.fontBytes > this.options.maxTotalFontBytes
        ) {
          throw new Error("google-font-total-size-limit-exceeded");
        }
        trustedCss.push(face.css);
        trustedFamilies.add(face.family);
        fontFiles += 1;
        totalFontBytes += face.fontBytes;
        loadedInlineFontFaces += 1;
      } catch (error) {
        failures.push({
          url: parsed?.fontUrl ?? "inline-google-font-face",
          reason: errorReason(error),
        });
      }
    }
    const loadedResources = loadedStylesheets + loadedInlineFontFaces;
    const status: GoogleFontsCollectionStatus =
      failures.length === 0
        ? "success"
        : loadedResources > 0
          ? "partial"
          : "unavailable";
    return {
      htmlForPreflight: extracted.htmlForPreflight,
      htmlForChrome: injectTrustedCss(
        extracted.htmlForPreflight,
        trustedCss.join("\n")
      ),
      status,
      reason: failures[0]?.reason,
      usedFallback: failures.length > 0,
      requestedStylesheets: extracted.stylesheets.length,
      requestedFontFaces: extracted.inlineFontFaceBodies.length,
      loadedStylesheets,
      fontFiles,
      trustedFamilies: [...trustedFamilies],
      failures,
    };
  }
}
