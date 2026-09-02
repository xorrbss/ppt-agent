/**
 * Server-side collector for fonts bundled with the package.
 *
 * Runs after the Google Fonts collector and before the untrusted preflight.
 * A referenced bundled family is delivered to the offline Chromium render as
 * validated `data:` @font-face rules, so the fidelity backplate and the text
 * measurement pass shape glyphs with the same face the OOXML packager embeds.
 * The sanitized `htmlForPreflight` document is never modified.
 */
import {
  BUNDLED_FONT_FAMILIES,
  type BundledFontFamily,
  loadBundledBrowserFace,
} from "./bundled-fonts.ts";
import type { PreparedGoogleFontsHtml } from "./google-fonts-collector.ts";
import { escapeCssString, injectTrustedCss } from "./google-fonts-css.ts";
import { getGoogleFontsCollector } from "./google-fonts-runtime.ts";
import { AUTHORED_RENDER_LIMITS } from "./security-data.ts";

export interface BundledFontCollectionResult {
  /** Families whose faces were injected into the Chrome document. */
  readonly families: readonly string[];
  /** Number of injected @font-face rules. */
  readonly fontFiles: number;
  readonly fontBytes: number;
  readonly failures: readonly { family: string; reason: string }[];
}

export type PreparedSlideFontsHtml = PreparedGoogleFontsHtml & {
  readonly bundled: BundledFontCollectionResult;
};

const BOLD_USAGE_PATTERN =
  /font-weight\s*:\s*(?:[6-9]00|bold|bolder)\b|<(?:b|strong|th|h[1-6])\b|\bfont-(?:semibold|bold|extrabold|black)\b/i;

function referencesFamily(html: string, family: string): boolean {
  const escaped = family.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
  return new RegExp(`font(?:-family)?\\s*:\\s*(?:[^;}]*[\\s,"'])?${escaped}(?=$|[\\s,"';}])`, "iu").test(html);
}

/** Families referenced by the authored document that this package can supply. */
export function referencedBundledFamilies(html: string): BundledFontFamily[] {
  return BUNDLED_FONT_FAMILIES.filter((family) => referencesFamily(html, family.family));
}

/** True when the document needs a bold face; the regular face is always sent. */
export function documentUsesBoldFace(html: string): boolean {
  return BOLD_USAGE_PATTERN.test(html);
}

function fontFaceCss(family: string, weight: number, data: Buffer): string {
  return (
    `@font-face{font-family:'${escapeCssString(family)}';font-style:normal;` +
    `font-weight:${weight};font-display:block;` +
    `src:url('data:font/woff2;base64,${data.toString("base64")}') format('woff2')}`
  );
}

/**
 * Inject bundled faces into an already collected document. The untrusted
 * `htmlForPreflight` is only read; the collector output `htmlForChrome`
 * receives the trusted CSS. Faces that would exceed the render limits are
 * skipped as a whole family so a partially delivered family never mixes a
 * bundled regular face with a synthesized bold one.
 */
export async function prepareBundledFontsHtml(
  prepared: PreparedGoogleFontsHtml,
): Promise<PreparedSlideFontsHtml> {
  const referenced = referencedBundledFamilies(prepared.htmlForPreflight);
  const families: string[] = [];
  const failures: { family: string; reason: string }[] = [];
  const css: string[] = [];
  let fontFiles = 0;
  let fontBytes = 0;
  const existingFaces = (prepared.htmlForChrome.match(/@font-face\b/gi) ?? []).length;
  const needsBold = documentUsesBoldFace(prepared.htmlForPreflight);
  for (const family of referenced) {
    const wanted = family.faces.filter((face) => face.weight === 400 || (needsBold && face.weight === 700));
    const files = await Promise.all(wanted.map((face) => loadBundledBrowserFace(family, face)));
    if (files.some((file) => file === undefined)) {
      failures.push({ family: family.family, reason: "bundled-font-file-missing" });
      continue;
    }
    const bytes = files.reduce((sum, file) => sum + file!.data.length, 0);
    if (
      existingFaces + fontFiles + files.length > AUTHORED_RENDER_LIMITS.maxFontFaces ||
      fontBytes + bytes > AUTHORED_RENDER_LIMITS.maxAggregateFontBytes
    ) {
      failures.push({ family: family.family, reason: "bundled-font-limit-exceeded" });
      continue;
    }
    files.forEach((file) => css.push(fontFaceCss(family.family, file!.weight, file!.data)));
    families.push(family.family);
    fontFiles += files.length;
    fontBytes += bytes;
  }
  return {
    ...prepared,
    htmlForChrome: css.length ? injectTrustedCss(prepared.htmlForChrome, css.join("\n")) : prepared.htmlForChrome,
    fontFiles: prepared.fontFiles + fontFiles,
    trustedFamilies: [...new Set([...prepared.trustedFamilies, ...families])],
    bundled: { families, fontFiles, fontBytes, failures },
  };
}

/** Google Fonts collection followed by bundled-font delivery for one slide. */
export async function collectSlideFonts(html: string): Promise<PreparedSlideFontsHtml> {
  return prepareBundledFontsHtml(await getGoogleFontsCollector().prepareHtml(html));
}
