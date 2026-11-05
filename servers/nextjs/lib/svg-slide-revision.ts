/** Revision and prefetch rules shared by the svg preview and the presenter. */

type SlideLike = {
  id?: string | null;
  svg_content?: unknown;
  content?: {
    svgAssetManifest?: { slideRevision?: string | number | null } | null;
    svgReceipt?: { source_digest?: string | number | null; fixedGlyphPreview?: unknown } | null;
  } | null;
};

export function resolveSvgSlideRevision(input: object): string | number | null | undefined {
  const slide = input as SlideLike;
  const manifest = slide.content?.svgAssetManifest;
  const receipt = slide.content?.svgReceipt;
  const fixed = receipt?.fixedGlyphPreview as
    | { version?: string; canonicalSvgDigest?: string }
    | null
    | undefined;
  if (fixed?.version === "svg-fixed-glyph-preview/v1") {
    return manifest?.slideRevision ?? fixed.canonicalSvgDigest;
  }
  return manifest?.slideRevision ?? receipt?.source_digest ?? (slide.svg_content as string | undefined);
}

/**
 * The (slideId, revision) pair whose materialized preview the presenter should
 * warm, or null when the slide renders from inline canonical SVG, is a
 * digest-verified fixed-glyph preview (scoped to its own view), or is not svg.
 */
export function svgPreviewPrefetchTarget(
  input: object | null | undefined,
): { slideId: string; revision: string } | null {
  const slide = input as SlideLike | null | undefined;
  if (!slide?.id || slide.content?.svgReceipt?.fixedGlyphPreview != null) return null;
  const inline = typeof slide.svg_content === "string" ? slide.svg_content : "";
  const isSvgSlide = inline !== "" || slide.content?.svgAssetManifest != null;
  if (!isSvgSlide) return null;
  if (inline.trim() !== "" && !inline.includes("source-slot-")) return null;
  const revision = resolveSvgSlideRevision(slide);
  if (revision == null || revision === "") return null;
  return { slideId: slide.id, revision: String(revision) };
}
