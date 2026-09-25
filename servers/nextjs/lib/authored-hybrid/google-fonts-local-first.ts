/**
 * Local-first short-circuit at the Google Fonts trust boundary.
 *
 * A family present in the bundled manifest is never requested from Google:
 * it is removed from the stylesheet URL before any fetch, and the bundled
 * collector later injects the local face under the same family name. Only
 * manifest families are touched; every other family keeps the exact URL the
 * document wrote, so a host without the manifest hit behaves as before.
 *
 * Each `family=` value names one family (`family=Noto+Sans+KR:wght@400;700`
 * on `/css2`, `family=Noto+Sans+KR:400,700` on legacy `/css`); the URL policy
 * already rejects the legacy `|`-joined form, so it is not handled here.
 */
import {
  isBundledFontFamily,
  normalizeBundledFamilyName,
} from "./bundled-fonts-manifest.ts";

export interface LocalFirstGoogleFontsUrl {
  /**
   * The validated URL with bundled families removed, or `null` when every
   * requested family is served locally and no request must be made at all.
   */
  readonly url: URL | null;
  /** Bundled families removed from the request, in document order. */
  readonly localFamilies: readonly string[];
}

function familyNameOfSpec(spec: string): string {
  return spec.split(":", 1)[0].trim().replace(/\s+/g, " ");
}

/**
 * Split one already-validated Google Fonts CSS URL into the remote part that
 * still needs fetching and the bundled families that do not.
 */
export function stripBundledFamiliesFromGoogleFontsCssUrl(
  url: URL,
): LocalFirstGoogleFontsUrl {
  const kept: string[] = [];
  const local: string[] = [];
  const seen = new Set<string>();
  for (const spec of url.searchParams.getAll("family")) {
    const name = familyNameOfSpec(spec);
    if (!name || !isBundledFontFamily(name)) {
      kept.push(spec);
      continue;
    }
    const key = normalizeBundledFamilyName(name);
    if (!seen.has(key)) {
      seen.add(key);
      local.push(name);
    }
  }
  if (!local.length) return { url, localFamilies: [] };
  if (!kept.length) return { url: null, localFamilies: local };
  const next = new URL(url.href);
  next.searchParams.delete("family");
  for (const family of kept) next.searchParams.append("family", family);
  return { url: next, localFamilies: local };
}
