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
