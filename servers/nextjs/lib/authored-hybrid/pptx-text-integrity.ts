import type { AuthoredHybridTextPayload } from "./schema.ts";

export const TEXT_BREAK_INTEGRITY_CODE = "text_break_integrity";

export class AuthoredHybridTextIntegrityError extends Error {
  readonly code = TEXT_BREAK_INTEGRITY_CODE;
  readonly slideNumber: number;
  readonly elementId: string;

  constructor(slideNumber: number, elementId: string) {
    super(`Hybrid text ${elementId} lost an authored line break.`);
    this.name = "AuthoredHybridTextIntegrityError";
    this.slideNumber = slideNumber;
    this.elementId = elementId;
  }
}

/**
 * Count only hard breaks carried by current browser observations.
 *
 * `paragraphs` is derived from `innerText`, which may insert a newline around a
 * blockified flex child even when Chromium paints every run on the same line.
 * Modern observations put genuine BR, preserved-source, and block boundaries
 * in newline runs with a hard breakKind. Paragraphs remain the compatibility
 * source only for legacy observations that have no runs.
 */
export function authoredHardBreakCount(text: AuthoredHybridTextPayload): number {
  if (text.runs.length > 0) {
    return text.runs.reduce(
      (count, run) => count + (
        run.breakKind === "line" || run.breakKind === "paragraph"
          ? (run.text.match(/\n/g) ?? []).length
          : 0
      ),
      0,
    );
  }
  return Math.max(0, text.paragraphs.length - 1);
}

export function assertSerializedTextBreakIntegrity(
  xml: string,
  text: AuthoredHybridTextPayload,
  slideNumber: number,
  elementId: string,
): void {
  const expected = authoredHardBreakCount(text);
  const serialized = (xml.match(/<a:br\/>/g) ?? []).length +
    Math.max(0, (xml.match(/<a:p>/g) ?? []).length - 1);
  if (serialized < expected) {
    throw new AuthoredHybridTextIntegrityError(slideNumber, elementId);
  }
}
