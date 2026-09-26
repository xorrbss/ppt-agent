export const DEFAULT_SLIDE_WIDTH_EMU = 12_192_000;
export const DEFAULT_SLIDE_HEIGHT_EMU = 6_858_000;
export const BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export function occurrences(xml: string, pattern: RegExp): number {
  return [...xml.matchAll(pattern)].length;
}

export function presentationSlideSize(
  presentationXml: string | undefined
): { width: number; height: number } {
  const match =
    presentationXml?.match(
      /<p:sldSz\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/
    ) ??
    presentationXml?.match(
      /<p:sldSz\b[^>]*\bcy="(\d+)"[^>]*\bcx="(\d+)"/
    );
  if (!match) {
    return {
      width: DEFAULT_SLIDE_WIDTH_EMU,
      height: DEFAULT_SLIDE_HEIGHT_EMU,
    };
  }
  const cxBeforeCy = match[0].indexOf("cx=") < match[0].indexOf("cy=");
  return cxBeforeCy
    ? { width: Number(match[1]), height: Number(match[2]) }
    : { width: Number(match[2]), height: Number(match[1]) };
}

export function pictureBlocks(xml: string): string[] {
  return [...xml.matchAll(/<p:pic\b[\s\S]*?<\/p:pic>/g)].map(
    (match) => match[0]
  );
}

export function pictureIdentity(block: string, index: number): string {
  const id = block.match(/<p:cNvPr\b[^>]*\bid="(\d+)"/)?.[1];
  return `ooxml:picture:${id ?? index + 1}`;
}

export function isHybridBackplate(block: string): boolean {
  return /<p:cNvPr\b[^>]*\bname="DevAX hybrid backplate"/.test(block);
}

