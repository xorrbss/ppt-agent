/**
 * Manifest of open-licensed fonts shipped inside the Next.js package.
 *
 * This is the single source of truth for "which logical family, at which
 * weight and style, is served from which local file". It is pure data plus
 * name/weight resolution so it can be imported from isomorphic modules such
 * as font-policy without pulling node:fs in; reading the bytes lives in
 * bundled-fonts.ts, delivery to Chrome in bundled-fonts-collector.ts, and
 * OOXML packaging in font-embedding-policy.ts.
 *
 * Only the regular (400) and bold (700) upright faces are shipped per family:
 * PowerPoint can only declare those two upright faces in `p:embeddedFontLst`,
 * and rendering the browser with the same two faces keeps text measurement
 * and native text aligned.
 *
 * Adding a family is "drop the files under public/fonts/<directory>/ and add
 * one entry here". A family is never resolved to a different family: a
 * lookup for a name that is not in the manifest is a miss, not a substitute.
 */

export type BundledFontWeight = 400 | 700;

export interface BundledFontFace {
  readonly weight: BundledFontWeight;
  readonly style: "normal";
  /** WOFF2 file used for the Chromium render document and the editor. */
  readonly browserFileName: string;
  /** Static TrueType file used for the PowerPoint EOT packager. */
  readonly ooxmlFileName: string;
}

export interface BundledFontFamily {
  readonly family: string;
  /** Directory under `public/fonts/`. */
  readonly directory: string;
  readonly license: "OFL-1.1";
  readonly licenseFileName: string;
  /** True when the family ships full Hangul syllable coverage. */
  readonly hangulCapable: boolean;
  readonly faces: readonly BundledFontFace[];
}

/** Same-origin path prefix under which Next.js serves `public/fonts`. */
export const BUNDLED_FONT_PUBLIC_PREFIX = "/fonts";

export const BUNDLED_FONT_FAMILIES: readonly BundledFontFamily[] = Object.freeze([
  {
    family: "Pretendard",
    directory: "pretendard",
    license: "OFL-1.1",
    licenseFileName: "LICENSE.txt",
    hangulCapable: true,
    faces: [
      {
        weight: 400,
        style: "normal",
        browserFileName: "Pretendard-Regular.woff2",
        ooxmlFileName: "Pretendard-Regular.ttf",
      },
      {
        weight: 700,
        style: "normal",
        browserFileName: "Pretendard-Bold.woff2",
        ooxmlFileName: "Pretendard-Bold.ttf",
      },
    ],
  },
]);

/**
 * Canonical lookup key for a CSS/OOXML family name: trimmed, unquoted,
 * whitespace-collapsed, lower-cased. Shared by every consumer so the same
 * spelling variants hit on both the render and the embedding seam.
 */
export function normalizeBundledFamilyName(value: string): string {
  return value
    .trim()
    .replace(/^(['"])(.*)\1$/u, "$2")
    .trim()
    .replace(/\s+/gu, " ")
    .toLocaleLowerCase("en-US");
}

const FAMILIES_BY_KEY: ReadonlyMap<string, BundledFontFamily> = new Map(
  BUNDLED_FONT_FAMILIES.map((entry) => [normalizeBundledFamilyName(entry.family), entry]),
);

/** Normalized names of bundled families that carry full Hangul coverage. */
export const HANGUL_CAPABLE_BUNDLED_FAMILY_KEYS: ReadonlySet<string> = new Set(
  BUNDLED_FONT_FAMILIES.filter((entry) => entry.hangulCapable).map((entry) =>
    normalizeBundledFamilyName(entry.family),
  ),
);

/** Exact-family lookup; `undefined` for any name that is not bundled. */
export function bundledFontFamily(family: string): BundledFontFamily | undefined {
  return FAMILIES_BY_KEY.get(normalizeBundledFamilyName(family));
}

export function isBundledFontFamily(family: string): boolean {
  return bundledFontFamily(family) !== undefined;
}

export interface BundledFontFaceRequest {
  readonly weight?: number | "normal" | "bold";
  readonly style?: "normal" | "italic" | "oblique";
}

function numericWeight(weight: BundledFontFaceRequest["weight"]): number {
  if (weight === undefined || weight === "normal") return 400;
  if (weight === "bold") return 700;
  return Number.isFinite(weight) ? weight : 400;
}

/**
 * Pick the shipped face Chromium would select for a requested weight under
 * the CSS Fonts 4 matching algorithm, restricted to the upright faces we
 * ship: a desired weight above 500 prefers the heavier face, 500 and below
 * prefer the lighter one. Italic requests return the upright face because
 * no italic is bundled and Chromium synthesizes the slant from it. Returns
 * `undefined` only when the family itself is not bundled.
 */
export function resolveBundledFontFace(
  family: string,
  request: BundledFontFaceRequest = {},
): { readonly family: BundledFontFamily; readonly face: BundledFontFace } | undefined {
  const entry = bundledFontFamily(family);
  if (!entry || !entry.faces.length) return undefined;
  const desired = numericWeight(request.weight);
  const ordered = [...entry.faces].sort((a, b) => a.weight - b.weight);
  const exact = ordered.find((face) => face.weight === desired);
  if (exact) return { family: entry, face: exact };
  const heavier = ordered.filter((face) => face.weight > desired);
  const lighter = ordered.filter((face) => face.weight < desired).reverse();
  const preferred = desired > 500 ? [...heavier, ...lighter] : [...lighter, ...heavier];
  return { family: entry, face: preferred[0] };
}

/** Same-origin URL Next.js serves for one bundled browser face. */
export function bundledFontPublicUrl(family: BundledFontFamily, face: BundledFontFace): string {
  return `${BUNDLED_FONT_PUBLIC_PREFIX}/${family.directory}/${face.browserFileName}`;
}

function familyReferencePattern(family: string): RegExp {
  const escaped = family.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
  return new RegExp(
    `font(?:-family)?\\s*:\\s*(?:[^;}]*[\\s,"'])?${escaped}(?=$|[\\s,"';}])`,
    "iu",
  );
}

/**
 * Bundled families named by `font-family`/`font` declarations in authored
 * markup. Prose that merely mentions a family name does not count.
 */
export function referencedBundledFamilies(html: string): BundledFontFamily[] {
  return BUNDLED_FONT_FAMILIES.filter((family) =>
    familyReferencePattern(family.family).test(html),
  );
}
