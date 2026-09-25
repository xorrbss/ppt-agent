import type { AuthoredHybridTextStyle } from "./schema.ts";

export interface PowerPointTextPiece {
  kind: "run" | "break";
  text: string;
  style: AuthoredHybridTextStyle;
}

/**
 * Join what PowerPoint would show as one stretch of text into one run.
 *
 * A soft wrap contributes an empty segment -- its newline is stripped, because
 * PowerPoint rewraps the paragraph itself -- which split the surrounding text
 * into separate `<a:r>` elements at every browser line. The text rendered the
 * same, but a sentence was spread over half a dozen runs, so editing it in
 * PowerPoint meant fighting run boundaries that correspond to nothing in the
 * source. Only identical run properties are merged, so rich text is untouched,
 * and a `break` piece is a real line break that must keep its neighbours apart.
 */
export function mergeAdjacentTextPieces(
  paragraph: readonly PowerPointTextPiece[],
): PowerPointTextPiece[] {
  const merged: PowerPointTextPiece[] = [];
  for (const piece of paragraph) {
    if (piece.kind === "run" && piece.text === "") continue;
    const previous = merged.at(-1);
    if (
      piece.kind === "run" &&
      previous?.kind === "run" &&
      JSON.stringify(previous.style) === JSON.stringify(piece.style)
    ) {
      merged[merged.length - 1] = { ...previous, text: previous.text + piece.text };
      continue;
    }
    merged.push(piece);
  }
  // A paragraph of nothing but empty runs keeps its original shape rather than
  // becoming a run-less `<a:p>`.
  return merged.length > 0 ? merged : [...paragraph];
}
