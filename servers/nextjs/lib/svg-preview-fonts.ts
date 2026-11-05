/**
 * Bundled fonts for the sandboxed SVG slide preview iframe.
 *
 * The server measures and renders svg slides with the bundled Noto Sans KR
 * and Pretendard faces (`servers/fastapi/services/svg_default_fonts.py`,
 * `svg_trusted_fonts.py`). The preview iframe has an opaque origin and a
 * strict CSP, so without an explicit `@font-face` the viewer's OS font would
 * be used and Korean text widths would differ from the measured layout.
 *
 * Only the families an SVG actually names get a face, and the iframe CSP
 * `font-src` lists exactly those font files (absolute URLs: `'self'` does not
 * match an opaque origin). The files are served with CORS by next.config.mjs
 * because a sandboxed document fetches fonts cross-origin.
 */
import {
  BUNDLED_FONT_PUBLIC_PREFIX,
  bundledFontFamily,
  normalizeBundledFamilyName,
} from "./authored-hybrid/bundled-fonts-manifest.ts";

export interface SvgPreviewFontFace {
  readonly family: string;
  /** Path below the public font prefix. */
  readonly path: string;
  readonly format: "woff2" | "truetype";
  /** CSS `font-weight` descriptor (a range for variable faces). */
  readonly weight: string;
}

/** Noto Sans KR ships as one variable TrueType file (no woff2 is bundled). */
const NOTO_SANS_KR_FACES: readonly SvgPreviewFontFace[] = Object.freeze([
  {
    family: "Noto Sans KR",
    path: "noto-sans-kr/NotoSansKR-Variable.ttf",
    format: "truetype",
    weight: "100 900",
  },
]);

function pretendardFaces(): readonly SvgPreviewFontFace[] {
  const entry = bundledFontFamily("Pretendard");
  if (!entry) return [];
  return entry.faces.map((face) => ({
    family: entry.family,
    path: `${entry.directory}/${face.browserFileName}`,
    format: "woff2" as const,
    weight: String(face.weight),
  }));
}

const FACES_BY_FAMILY: ReadonlyMap<string, readonly SvgPreviewFontFace[]> = new Map([
  [normalizeBundledFamilyName("Pretendard"), pretendardFaces()],
  [normalizeBundledFamilyName("Noto Sans KR"), NOTO_SANS_KR_FACES],
]);

/** Bundled families the SVG source names in `font-family` attributes/styles. */
export function bundledFamiliesUsedBySvg(svg: string): string[] {
  const used = new Set<string>();
  // Attribute values are quoted: font-family="Pretendard, 'Noto Sans KR'".
  const sources = [
    ...svg.matchAll(/font-family\s*=\s*"([^"]*)"/giu),
    ...svg.matchAll(/font-family\s*=\s*'([^']*)'/giu),
    ...svg.matchAll(/font-family\s*:\s*([^;"}>]*)/giu),
  ].map((match) => match[1]);
  for (const stack of sources) {
    for (const candidate of stack.replace(/&quot;|&apos;|&#x27;|&#39;/giu, "").split(",")) {
      const key = normalizeBundledFamilyName(candidate);
      if (FACES_BY_FAMILY.has(key)) used.add(key);
    }
  }
  return [...FACES_BY_FAMILY.keys()].filter((key) => used.has(key));
}

export function previewFontFacesForSvg(svg: string): SvgPreviewFontFace[] {
  return bundledFamiliesUsedBySvg(svg).flatMap((key) => [...(FACES_BY_FAMILY.get(key) ?? [])]);
}

export function previewFontUrl(origin: string, face: SvgPreviewFontFace): string {
  return `${origin}${BUNDLED_FONT_PUBLIC_PREFIX}/${face.path}`;
}

/** `@font-face` rules for exactly the given faces. */
export function previewFontFaceCss(origin: string, faces: readonly SvgPreviewFontFace[]): string {
  return faces
    .map(
      (face) =>
        `@font-face{font-family:'${face.family}';font-style:normal;font-weight:${face.weight};` +
        `font-display:block;src:url(${previewFontUrl(origin, face)}) format('${face.format}');}`,
    )
    .join("");
}

/** `font-src` directive value: the exact font files, or `'none'`. */
export function previewFontSrc(origin: string, faces: readonly SvgPreviewFontFace[]): string {
  if (faces.length === 0) return "'none'";
  return faces.map((face) => previewFontUrl(origin, face)).join(" ");
}

/**
 * `fontSrc` is `'none'`, `data:` (digest-verified fixed-glyph previews embed
 * their faces) or the exact bundled font file URLs the SVG names.
 */
export function buildSvgPreviewCsp(fontSrc: string): string {
  return [
    "default-src 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "connect-src 'none'",
    "img-src data:",
    `font-src ${fontSrc}`,
    "style-src 'unsafe-inline'",
    "script-src 'none'",
  ].join("; ");
}

/** The iframe document for a sanitized SVG; the iframe stays `sandbox=""`. */
export function buildSvgPreviewSrcDoc(svgSource: string, origin: string, fixedGlyph: boolean): string {
  const withoutForeignDocument = svgSource
    .replace(/<script\b[\s\S]*?<\/script\s*>/gi, "")
    .replace(/<foreignObject\b[\s\S]*?<\/foreignObject\s*>/gi, "");
  // Fixed-glyph SVGs carry their own faces; every other SVG gets the bundled
  // families it names so widths match the server measurement.
  const faces = fixedGlyph ? [] : previewFontFacesForSvg(withoutForeignDocument);
  const csp = buildSvgPreviewCsp(fixedGlyph ? "data:" : previewFontSrc(origin, faces));
  const fontCss = previewFontFaceCss(origin, faces);
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${csp}"><style>${fontCss}html,body{margin:0;width:100%;height:100%;overflow:hidden}body>svg{display:block;width:100%;height:100%}</style><body>${withoutForeignDocument}</body>`;
}
