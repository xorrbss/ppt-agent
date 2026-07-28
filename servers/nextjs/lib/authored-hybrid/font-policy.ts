export interface AuthoredFontFallbackRule {
  readonly authored: string;
  readonly powerpoint: string;
}

/**
 * One compatibility policy is shared by Chromium layout capture and native
 * PowerPoint typeface serialization. Source-fidelity CSS keeps the authored
 * face first. An editable-layout measurement pass can instead put the mapped
 * PowerPoint face first so browser line breaks are measured with the same
 * installed typeface that PowerPoint will use.
 *
 * This is typeface selection only. The server-side embedding pipeline may
 * explicitly preserve a successfully embedded authored family.
 */
export const AUTHORED_FONT_FALLBACK_POLICY = Object.freeze([
  { authored: "Noto Sans KR", powerpoint: "Malgun Gothic" },
  { authored: "Pretendard", powerpoint: "Malgun Gothic" },
  { authored: "Noto Serif KR", powerpoint: "Batang" },
  { authored: "Inter", powerpoint: "Aptos" },
  { authored: "Roboto", powerpoint: "Aptos" },
  { authored: "DM Sans", powerpoint: "Aptos" },
  { authored: "Source Serif 4", powerpoint: "Cambria" },
  { authored: "IBM Plex Mono", powerpoint: "Consolas" },
] as const satisfies readonly AuthoredFontFallbackRule[]);

export const POWERPOINT_GENERIC_FONT_FALLBACK_POLICY = Object.freeze([
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

const POLICY_BY_AUTHORED = new Map(
  AUTHORED_FONT_FALLBACK_POLICY.map((rule) => [
    rule.authored.toLowerCase(),
    rule.powerpoint,
  ])
);

function normalizeFamily(family: string): string {
  return family.trim().replace(/^['"]|['"]$/g, "");
}

export function resolveAuthoredFontFallback(
  family: string
): string | undefined {
  const normalized = normalizeFamily(family).toLowerCase();
  return POLICY_BY_AUTHORED.get(normalized) ?? GENERIC_FALLBACKS.get(normalized);
}

export function expandAuthoredFontFamilyStack(
  families: readonly string[]
): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  const append = (family: string) => {
    const normalized = normalizeFamily(family);
    const key = normalized.toLowerCase();
    if (!normalized || seen.has(key)) return;
    seen.add(key);
    result.push(normalized);
  };

  for (const family of families) {
    append(family);
    const fallback = POLICY_BY_AUTHORED.get(normalizeFamily(family).toLowerCase());
    if (fallback) append(fallback);
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
  const append = (family: string) => {
    const normalized = normalizeFamily(family);
    const key = normalized.toLowerCase();
    if (!normalized || seen.has(key)) return;
    seen.add(key);
    result.push(normalized);
  };

  for (const family of families) {
    const normalized = normalizeFamily(family);
    const mapped = resolveAuthoredFontFallback(normalized);
    if (mapped) append(mapped);
    append(normalized);
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
  const generic = new Set([
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
  ]);
  const preservedFamilies = new Set(
    [...(options.preserveAuthoredFamilies ?? [])].map((family) =>
      normalizeFamily(family).toLowerCase()
    )
  );
  for (const family of families) {
    const normalized = normalizeFamily(family);
    if (!normalized) continue;
    if (preservedFamilies.has(normalized.toLowerCase())) {
      return normalized.slice(0, 127);
    }
    const mapped = resolveAuthoredFontFallback(normalized);
    if (mapped) return mapped.slice(0, 127);
    if (!generic.has(normalized.toLowerCase())) return normalized.slice(0, 127);
  }
  return fallback.trim().slice(0, 127);
}
