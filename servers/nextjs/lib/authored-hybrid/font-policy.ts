/**
 * Logical authored fonts and their deterministic Office/Windows substitutes.
 *
 * Google Fonts may be fetched by the trusted server-side collector for browser
 * rendering, but authored-hybrid does not embed those font programs in OOXML.
 * Native PPTX text and collector failure therefore use the local typeface in
 * this registry. Keep CSS rewriting and native-plan serialization on these
 * helpers so the two paths cannot silently choose different substitutes.
 */
export interface AuthoredFontFallback {
  logical: string;
  local: string;
  generic: "sans-serif" | "serif" | "monospace";
}

export type AuthoredFontScript = "cjk" | "latin" | "common";

export type AuthoredEmbeddedFontFace =
  | "regular"
  | "bold"
  | "italic"
  | "boldItalic";

/** Exact face evidence produced by the OOXML font packager. */
export interface AuthoredEmbeddedTypefaceFaceEvidence {
  readonly typeface: string;
  readonly face: AuthoredEmbeddedFontFace;
}

export interface AuthoredScriptRun {
  readonly text: string;
  readonly start: number;
  readonly end: number;
  readonly script: AuthoredFontScript;
}

const CJK_CHARACTER =
  /[\u2e80-\u2fff\u3005-\u3007\u3040-\u30ff\u3100-\u312f\u31a0-\u31bf\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]/u;
const LATIN_OR_NUMBER_CHARACTER = /[\p{Script=Latin}\p{Number}]/u;

function characterScript(value: string): AuthoredFontScript {
  if (CJK_CHARACTER.test(value)) return "cjk";
  if (LATIN_OR_NUMBER_CHARACTER.test(value)) return "latin";
  return "common";
}

/**
 * Split text at Unicode code-point boundaries. Neutral punctuation and spaces
 * stay with the preceding strong script, or the following script when leading,
 * so mixed Korean/Latin labels do not turn every separator into its own run.
 */
export function splitTextByScript(value: string): AuthoredScriptRun[] {
  if (!value) return [];
  const codePoints = Array.from(value);
  const scripts = codePoints.map(characterScript);
  for (let index = 0; index < scripts.length; index += 1) {
    if (scripts[index] !== "common") continue;
    const previous = index > 0 ? scripts[index - 1] : undefined;
    if (previous && previous !== "common") {
      scripts[index] = previous;
      continue;
    }
    const following = scripts.slice(index + 1).find((script) => script !== "common");
    if (following) scripts[index] = following;
  }

  const result: AuthoredScriptRun[] = [];
  let utf16Offset = 0;
  let runStart = 0;
  let runText = "";
  let runScript = scripts[0];
  codePoints.forEach((codePoint, index) => {
    const script = scripts[index];
    if (runText && script !== runScript) {
      result.push({ text: runText, start: runStart, end: utf16Offset, script: runScript });
      runStart = utf16Offset;
      runText = "";
      runScript = script;
    }
    runText += codePoint;
    utf16Offset += codePoint.length;
  });
  result.push({ text: runText, start: runStart, end: utf16Offset, script: runScript });
  return result;
}

export function embeddedFaceForStyle(style: {
  readonly bold: boolean;
  readonly italic: boolean;
  readonly fontWeight: number;
}): AuthoredEmbeddedFontFace {
  const bold = style.bold || style.fontWeight >= 600;
  if (bold && style.italic) return "boldItalic";
  if (bold) return "bold";
  return style.italic ? "italic" : "regular";
}

export function hasEmbeddedTypefaceFace(
  evidence: readonly AuthoredEmbeddedTypefaceFaceEvidence[],
  typeface: string,
  face: AuthoredEmbeddedFontFace
): boolean {
  const expected = normalizedFontName(typeface);
  return evidence.some(
    (entry) =>
      normalizedFontName(entry.typeface) === expected && entry.face === face
  );
}

export const FONT_FALLBACK_REGISTRY: readonly AuthoredFontFallback[] =
  Object.freeze([
    { logical: "Noto Sans KR", local: "Malgun Gothic", generic: "sans-serif" },
    { logical: "Pretendard", local: "Malgun Gothic", generic: "sans-serif" },
    { logical: "Noto Serif KR", local: "Batang", generic: "serif" },
    { logical: "Inter", local: "Aptos", generic: "sans-serif" },
    { logical: "Roboto", local: "Aptos", generic: "sans-serif" },
    { logical: "DM Sans", local: "Aptos", generic: "sans-serif" },
    { logical: "Source Serif 4", local: "Cambria", generic: "serif" },
    { logical: "IBM Plex Mono", local: "Consolas", generic: "monospace" },
  ] as const);

const GENERIC_FAMILIES = new Set([
  "serif",
  "sans-serif",
  "monospace",
  "cursive",
  "fantasy",
  "system-ui",
  "ui-serif",
  "ui-sans-serif",
  "ui-monospace",
]);

function normalizedFontName(value: string): string {
  return value
    .trim()
    .replace(/^(['"])(.*)\1$/u, "$2")
    .replace(/\s+/gu, " ")
    .toLocaleLowerCase("en-US");
}

const FALLBACK_BY_LOGICAL = new Map(
  FONT_FALLBACK_REGISTRY.map((entry) => [
    normalizedFontName(entry.logical),
    entry,
  ])
);

export function isGenericFontFamily(value: string): boolean {
  return GENERIC_FAMILIES.has(normalizedFontName(value));
}

/**
 * Resolve one logical family to the local typeface stored in editable PPTX.
 * Unknown local/custom families are retained unchanged.
 */
export function resolveFontFamily(value: string): string {
  const trimmed = value.trim().replace(/^(['"])(.*)\1$/u, "$2").trim();
  return FALLBACK_BY_LOGICAL.get(normalizedFontName(trimmed))?.local ?? trimmed;
}

/**
 * Resolve the first non-generic CSS family for OOXML, with a deterministic
 * fallback when a CSS stack contains only generic names.
 */
export function resolveFontCandidates(
  candidates: readonly string[],
  fallback: string,
  trustedFamilies: readonly string[] = []
): string {
  const trusted = new Set(trustedFamilies.map(normalizedFontName));
  const trustedSelected = candidates.find(
    (candidate) =>
      candidate.trim() &&
      !isGenericFontFamily(candidate) &&
      trusted.has(normalizedFontName(candidate))
  );
  const selected = trustedSelected ?? candidates.find(
    (candidate) => candidate.trim() && !isGenericFontFamily(candidate)
  );
  if (trustedSelected) {
    return trustedSelected
      .trim()
      .replace(/^(['"])(.*)\1$/u, "$2")
      .trim()
      .slice(0, 127);
  }
  return resolveFontFamily(selected ?? fallback).slice(0, 127);
}

function quoteCssFamily(value: string): string {
  const safe = value.replace(/\\/gu, "\\\\").replace(/"/gu, '\\"');
  return `"${safe}"`;
}

/**
 * CSS stack used when a logical web font is unavailable. The logical family
 * remains first so a verified collector-provided @font-face can win; the same
 * local typeface returned by resolveFontFamily is second.
 */
export function cssFontStack(value: string): string {
  const trimmed = value.trim().replace(/^(['"])(.*)\1$/u, "$2").trim();
  const entry = FALLBACK_BY_LOGICAL.get(normalizedFontName(trimmed));
  if (!entry) return quoteCssFamily(trimmed);
  return [
    quoteCssFamily(entry.logical),
    quoteCssFamily(entry.local),
    entry.generic,
  ].join(", ");
}

function splitCssFontFamilies(value: string): string[] {
  const families: string[] = [];
  let quote = "";
  let escaped = false;
  let start = 0;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "\\") {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = "";
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
    } else if (character === ",") {
      families.push(value.slice(start, index).trim());
      start = index + 1;
    }
  }
  families.push(value.slice(start).trim());
  return families.filter(Boolean);
}

function splitImportantSuffix(value: string): {
  declaration: string;
  important: string;
} {
  const match = value.match(/^(.*?)(\s*!important\s*)$/iu);
  return match
    ? { declaration: match[1].trim(), important: " !important" }
    : { declaration: value.trim(), important: "" };
}

/**
 * Expand known logical families inside one CSS font-family value. Generic
 * families are kept at the end so they do not mask a later CJK/local fallback.
 */
export function expandKnownFontFamilyList(value: string): string {
  const { declaration, important } = splitImportantSuffix(value);
  const parsed = splitCssFontFamilies(declaration);
  const known = parsed
    .map((family) =>
      FALLBACK_BY_LOGICAL.get(normalizedFontName(family))
    )
    .filter((entry): entry is AuthoredFontFallback => entry !== undefined);
  if (!known.length) return value.trim();

  const output: string[] = [];
  const seen = new Set<string>();
  const push = (family: string) => {
    const key = normalizedFontName(family);
    if (!seen.has(key)) {
      seen.add(key);
      output.push(family);
    }
  };
  for (const family of parsed) {
    const entry = FALLBACK_BY_LOGICAL.get(normalizedFontName(family));
    if (entry) {
      push(quoteCssFamily(entry.logical));
      push(quoteCssFamily(entry.local));
    } else if (!isGenericFontFamily(family)) {
      push(family);
    }
  }
  for (const family of parsed) {
    if (isGenericFontFamily(family)) push(normalizedFontName(family));
  }
  if (!output.some(isGenericFontFamily)) push(known[0].generic);
  return `${output.join(", ")}${important}`;
}

function regexEscape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

function expandKnownFontShorthand(value: string): string {
  const { declaration, important } = splitImportantSuffix(value);
  let expanded = declaration;
  let firstMatch: AuthoredFontFallback | undefined;
  for (const entry of FONT_FALLBACK_REGISTRY) {
    const logical = regexEscape(entry.logical).replace(/\s+/gu, "\\s+");
    const pattern = new RegExp(
      `(?:([\"'])${logical}\\1|(?<![\\p{L}\\p{N}_-])${logical}(?![\\p{L}\\p{N}_-]))`,
      "giu"
    );
    expanded = expanded.replace(pattern, () => {
      firstMatch ??= entry;
      return `${quoteCssFamily(entry.logical)}, ${quoteCssFamily(entry.local)}`;
    });
  }
  if (
    firstMatch &&
    !/(?:^|[,\s])(?:serif|sans-serif|monospace)(?=$|[,\s])/iu.test(expanded)
  ) {
    expanded = `${expanded}, ${firstMatch.generic}`;
  }
  return `${expanded}${important}`;
}

/**
 * Rewrite font-family declarations in a bounded CSS fragment (a <style>
 * payload or one inline style attribute). This intentionally does not parse
 * HTML; callers must select only CSS-bearing trust-boundary content.
 */
export function rewriteKnownFontFamiliesInCss(css: string): string {
  const familyLists = css.replace(
    /(\bfont-family\s*:\s*)([^;}{]+?)(?=\s*(?:[;}]|$))/giu,
    (_match, prefix: string, value: string) =>
      `${prefix}${expandKnownFontFamilyList(value.trim())}`
  );
  return familyLists.replace(
    /(\bfont(?!-)\s*:\s*)([^;}{]+?)(?=\s*(?:[;}]|$))/giu,
    (_match, prefix: string, value: string) =>
      `${prefix}${expandKnownFontShorthand(value.trim())}`
  );
}

/* --------------------------------------------------------------------------
 * PowerPoint typeface selection.
 *
 * These helpers drive Chromium layout capture and native OOXML typeface
 * serialization. They derive from FONT_FALLBACK_REGISTRY above rather than
 * carrying a second table, so CSS rewriting and native serialization cannot
 * drift onto different substitutes for the same logical face.
 * ----------------------------------------------------------------------- */

export interface AuthoredFontFallbackRule {
  readonly authored: string;
  readonly powerpoint: string;
}

export const AUTHORED_FONT_FALLBACK_POLICY: readonly AuthoredFontFallbackRule[] =
  Object.freeze(
    FONT_FALLBACK_REGISTRY.map((entry) =>
      Object.freeze({ authored: entry.logical, powerpoint: entry.local })
    )
  );

/**
 * Generic CSS families have no logical entry in the registry, so their
 * PowerPoint substitutes are declared here.
 */
export const POWERPOINT_GENERIC_FONT_FALLBACK_POLICY: readonly AuthoredFontFallbackRule[] =
  Object.freeze([
    { authored: "sans-serif", powerpoint: "Aptos" },
    { authored: "system-ui", powerpoint: "Aptos" },
    { authored: "serif", powerpoint: "Cambria" },
    { authored: "monospace", powerpoint: "Consolas" },
  ] as const satisfies readonly AuthoredFontFallbackRule[]);

const GENERIC_FALLBACKS = new Map<string, string>(
  POWERPOINT_GENERIC_FONT_FALLBACK_POLICY.map((rule) => [
    rule.authored,
    rule.powerpoint,
  ])
);

export function resolveAuthoredFontFallback(
  family: string
): string | undefined {
  const normalized = normalizedFontName(family);
  return (
    FALLBACK_BY_LOGICAL.get(normalized)?.local ??
    GENERIC_FALLBACKS.get(normalized)
  );
}

function appendUnique(
  result: string[],
  seen: Set<string>,
  family: string
): void {
  const normalized = family.trim().replace(/^['"]|['"]$/g, "");
  const key = normalized.toLowerCase();
  if (!normalized || seen.has(key)) return;
  seen.add(key);
  result.push(normalized);
}

export function expandAuthoredFontFamilyStack(
  families: readonly string[]
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const family of families) {
    appendUnique(result, seen, family);
    const fallback = FALLBACK_BY_LOGICAL.get(normalizedFontName(family))?.local;
    if (fallback) appendUnique(result, seen, fallback);
  }
  return result;
}

/**
 * Builds a mapped-first CSS stack for the editable text measurement pass.
 *
 * Keep this separate from `expandAuthoredFontFamilyStack`: the source-fidelity
 * backplate still needs the collected authored face first, while native text
 * geometry needs the PowerPoint-compatible face first. Keeping the authored
 * face second also preserves a deterministic fallback when the mapped local
 * face is unavailable.
 */
export function expandPowerPointLayoutFontFamilyStack(
  families: readonly string[]
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const family of families) {
    const normalized = family.trim().replace(/^['"]|['"]$/g, "");
    const mapped = resolveAuthoredFontFallback(normalized);
    if (mapped) appendUnique(result, seen, mapped);
    appendUnique(result, seen, normalized);
  }
  return result;
}

export function resolvePowerPointTypeface(
  families: readonly string[],
  fallback: string,
  options: {
    /**
     * Families in this set were actually packaged into the PPTX. Merely
     * finding an eligible local font is not sufficient to preserve it.
     */
    preserveAuthoredFamilies?: ReadonlySet<string>;
  } = {}
): string {
  const preservedFamilies = new Set(
    [...(options.preserveAuthoredFamilies ?? [])].map((family) =>
      normalizedFontName(family)
    )
  );
  for (const family of families) {
    const normalized = family.trim().replace(/^['"]|['"]$/g, "");
    if (!normalized) continue;
    if (preservedFamilies.has(normalizedFontName(normalized))) {
      return normalized.slice(0, 127);
    }
    const mapped = resolveAuthoredFontFallback(normalized);
    if (mapped) return mapped.slice(0, 127);
    if (!isGenericFontFamily(normalized)) return normalized.slice(0, 127);
  }
  return fallback.trim().slice(0, 127);
}

const MONOSPACE_TYPEFACES = new Set([
  "ibm plex mono",
  "consolas",
  "monospace",
  "ui-monospace",
]);

const CJK_SERIF_TYPEFACES = new Set([
  "serif",
  "ui-serif",
  "noto serif kr",
  "noto serif cjk kr",
  "source serif 4",
  "batang",
  "batangche",
  "cambria",
]);

export function isCjkSerifFontStack(families: readonly string[]): boolean {
  return families.some((family) =>
    CJK_SERIF_TYPEFACES.has(normalizedFontName(family))
  );
}

export function isMonospaceFontStack(families: readonly string[]): boolean {
  return families.some((family) =>
    MONOSPACE_TYPEFACES.has(normalizedFontName(family))
  );
}

export interface AuthoredScriptTypefaceResolution {
  readonly typeface: string;
  readonly fallbackTypeface: string;
  readonly requiresEmbedding: boolean;
}

/**
 * Script-aware native policy. CJK never inherits a Latin monospace face.
 * Conversely, the IBM Plex Mono/Consolas pair is considered only for a run
 * that contains Latin letters or numbers and whose authored stack requested
 * monospace styling.
 */
export function resolveScriptAwareTypeface(
  script: AuthoredFontScript,
  families: readonly string[]
): AuthoredScriptTypefaceResolution {
  if (script === "cjk") {
    if (isCjkSerifFontStack(families)) {
      return {
        typeface: "Noto Serif KR",
        fallbackTypeface: "Batang",
        requiresEmbedding: true,
      };
    }
    return {
      typeface: "Noto Sans KR",
      fallbackTypeface: "Malgun Gothic",
      requiresEmbedding: true,
    };
  }
  if (script === "latin" && isMonospaceFontStack(families)) {
    return {
      typeface: "IBM Plex Mono",
      fallbackTypeface: "Consolas",
      requiresEmbedding: true,
    };
  }
  const typeface = resolvePowerPointTypeface(families, "Aptos", {
    preserveAuthoredFamilies: new Set(families),
  });
  return {
    typeface,
    fallbackTypeface: resolveFontFamily(typeface) || "Aptos",
    requiresEmbedding:
      resolveFontFamily(typeface).toLocaleLowerCase("en-US") !==
      typeface.toLocaleLowerCase("en-US"),
  };
}
