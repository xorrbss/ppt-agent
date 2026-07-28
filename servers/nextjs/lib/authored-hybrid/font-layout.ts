import type {
  AuthoredHybridElement,
  AuthoredHybridTextPayload,
} from "./schema.ts";

export interface PowerPointTextLayoutMergeResult {
  elements: AuthoredHybridElement[];
  appliedTextElements: number;
}

export interface PowerPointTextLayoutMergeOptions {
  /**
   * Families for which OOXML packaging has already succeeded. Those elements
   * retain the authored browser geometry because PowerPoint can use the same
   * typeface instead of the mapped fallback metrics.
   */
  embeddedTypefaceFamilies?: readonly string[];
}

function textPayload(
  element: AuthoredHybridElement
): AuthoredHybridTextPayload | undefined {
  return "text" in element ? element.text : undefined;
}

function normalizedTypefaceName(value: string): string {
  return value.trim().replace(/^['"]|['"]$/g, "").toLowerCase();
}

function usesEmbeddedTypeface(
  text: AuthoredHybridTextPayload,
  embeddedFamilies: ReadonlySet<string>
): boolean {
  const styles = [text.style, ...text.runs.map((run) => run.style)];
  return styles.some((style) =>
    style.fontFamilies.some((family) =>
      embeddedFamilies.has(normalizedTypefaceName(family))
    )
  );
}

/**
 * Applies only text geometry from a mapped-font extraction to the
 * source-fidelity contract. Authored typefaces and run emphasis remain in the
 * contract so the OOXML serializer can choose between an actually embedded
 * family and the central fallback policy. Classification, paint order,
 * container paint, and non-text elements remain anchored to the source render
 * used by the backplate. Identity mismatches fail closed at element
 * granularity.
 */
export function mergePowerPointTextLayout(
  sourceElements: readonly AuthoredHybridElement[],
  layoutElements: readonly AuthoredHybridElement[],
  options: PowerPointTextLayoutMergeOptions = {}
): PowerPointTextLayoutMergeResult {
  const embeddedFamilies = new Set(
    (options.embeddedTypefaceFamilies ?? []).map(normalizedTypefaceName)
  );
  const layoutById = new Map(
    layoutElements.map((element) => [element.id, element])
  );
  let appliedTextElements = 0;
  const elements = sourceElements.map((source) => {
    const sourceText = textPayload(source);
    if (!sourceText) return source;
    if (
      embeddedFamilies.size > 0 &&
      usesEmbeddedTypeface(sourceText, embeddedFamilies)
    ) {
      return source;
    }
    const layout = layoutById.get(source.id);
    const layoutText = layout ? textPayload(layout) : undefined;
    if (
      !layout ||
      !layoutText ||
      layout.domPath !== source.domPath ||
      layout.tagName !== source.tagName ||
      layout.sourceIndex !== source.sourceIndex ||
      layoutText.role !== sourceText.role ||
      layoutText.plainText !== sourceText.plainText
    ) {
      return source;
    }
    appliedTextElements += 1;
    return {
      ...source,
      bounds: layout.bounds,
      text: {
        ...layoutText,
        // The mapped-font pass is allowed to change geometry and typeface
        // metrics, but it must not become the source of visual line breaks.
        // Chromium may wrap Malgun Gothic at different syllables than the
        // authored Pretendard/Noto layout. Keep the source pass' explicit
        // visual-line segmentation, root style, and run styling. The OOXML
        // serializer selects the embedded family only after font packaging
        // succeeds; every other path continues through the central fallback.
        plainText: sourceText.plainText,
        paragraphs: sourceText.paragraphs,
        style: sourceText.style,
        runs: sourceText.runs,
        // Background/border paint belongs to the source contract and
        // backplate identity, not to a font-metric measurement pass.
        containerShape: sourceText.containerShape,
      },
    } as AuthoredHybridElement;
  });
  return { elements, appliedTextElements };
}
