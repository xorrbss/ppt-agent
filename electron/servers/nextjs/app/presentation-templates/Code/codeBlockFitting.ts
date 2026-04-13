const DEFAULT_CODE_CHAR_WIDTH_RATIO = 0.62;
const DEFAULT_CODE_LINE_HEIGHT_RATIO = 1.25;
const DEFAULT_FONT_STEP = 0.5;
const HARD_MIN_FONT_SIZE = 4;

export const DEFAULT_CODE_FONT_FAMILY = "var(--code-font-family,'Liberation Mono', monospace)";

interface FitCodeBlockOptions {
  language?: string;
  content?: string;
  maxWidth: number;
  maxHeight: number;
  maxFontSize?: number;
  minFontSize?: number;
  fontStep?: number;
  charWidthRatio?: number;
  lineHeightRatio?: number;
}

interface TypographyCandidate {
  lineHeight: number;
  maxCharsPerLine: number;
  wrappedLines: string[];
}

export interface FittedCodeBlock {
  text: string;
  fontSize: number;
  lineHeight: number;
  fontFamily: string;
}

function splitCollapsedPythonImports(line: string) {
  const importSegments = line
    .split(/(?=\sfrom\s+[A-Za-z0-9_.]+\s+import\s+)/g)
    .map((segment) => segment.trim())
    .filter(Boolean);

  return importSegments.length > 1 ? importSegments : [line];
}

function expandInlinePythonStatement(line: string) {
  const inlineReturnMatch = line.match(/^(\s*def\s+[^(]+\([^)]*\):)\s+return\s+(.+)$/);

  if (!inlineReturnMatch) {
    return [line];
  }

  return [inlineReturnMatch[1], `    return ${inlineReturnMatch[2]}`];
}

function expandPathListAssignment(line: string) {
  const trimmedLine = line.trim();

  if (!trimmedLine.startsWith("urlpatterns = [") || !trimmedLine.endsWith("]")) {
    return [line];
  }

  const pathCalls = trimmedLine.match(/path\([^)]*\)/g);

  if (!pathCalls?.length) {
    return [line];
  }

  return [
    "urlpatterns = [",
    ...pathCalls.map((pathCall) => `    ${pathCall},`),
    "]",
  ];
}

function normalizePythonCode(content: string) {
  const normalizedLines: string[] = [];

  for (const line of content.split("\n")) {
    const importLines = splitCollapsedPythonImports(line);

    for (const importLine of importLines) {
      const expandedPathLines = expandPathListAssignment(importLine);

      for (const expandedPathLine of expandedPathLines) {
        normalizedLines.push(...expandInlinePythonStatement(expandedPathLine));
      }
    }
  }

  return normalizedLines.join("\n").replace(/\n{3,}/g, "\n\n");
}

function tryFormatJson(content: string) {
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

export function normalizeCodeContent(language?: string, content?: string) {
  let normalizedContent = (content || "")
    .replace(/\r\n?/g, "\n")
    .replace(/\\\[/g, "[")
    .replace(/\\\]/g, "]")
    .trimEnd();

  const normalizedLanguage = language?.toLowerCase();
  const isJsonLanguage = normalizedLanguage?.includes("json");
  const looksLikeJsonPayload = !normalizedLanguage && /^[\[{]/.test(normalizedContent.trim());

  if (normalizedLanguage === "python") {
    normalizedContent = normalizePythonCode(normalizedContent);
  } else if (isJsonLanguage || looksLikeJsonPayload) {
    normalizedContent = tryFormatJson(normalizedContent);
  }

  return normalizedContent;
}

function wrapLineWithContinuation(line: string, maxCharsPerLine: number) {
  if (line.length <= maxCharsPerLine) {
    return [line];
  }

  const leadingWhitespace = line.match(/^\s*/)?.[0] ?? "";
  const continuationPrefix = `${leadingWhitespace}  `;
  const continuationCapacity = maxCharsPerLine - continuationPrefix.length;

  if (continuationCapacity <= 8) {
    const chunks: string[] = [];
    for (let start = 0; start < line.length; start += maxCharsPerLine) {
      chunks.push(line.slice(start, start + maxCharsPerLine));
    }
    return chunks;
  }

  const chunks = [line.slice(0, maxCharsPerLine)];
  for (
    let start = maxCharsPerLine;
    start < line.length;
    start += continuationCapacity
  ) {
    chunks.push(`${continuationPrefix}${line.slice(start, start + continuationCapacity)}`);
  }
  return chunks;
}

function wrapContentToWidth(content: string, maxCharsPerLine: number) {
  const wrappedLines: string[] = [];
  const rawLines = content.split("\n");

  for (const rawLine of rawLines) {
    const expandedLine = rawLine.replace(/\t/g, "  ");

    if (expandedLine.length === 0) {
      wrappedLines.push("");
      continue;
    }

    wrappedLines.push(...wrapLineWithContinuation(expandedLine, maxCharsPerLine));
  }

  return wrappedLines.length ? wrappedLines : [""];
}

function createTypographyCandidate(
  normalizedContent: string,
  fontSize: number,
  maxWidth: number,
  charWidthRatio: number,
  lineHeightRatio: number
): TypographyCandidate {
  const lineHeight = Math.max(1, Math.round(fontSize * lineHeightRatio));
  const maxCharsPerLine = Math.max(1, Math.floor(maxWidth / (fontSize * charWidthRatio)));
  const wrappedLines = wrapContentToWidth(normalizedContent, maxCharsPerLine);

  return {
    lineHeight,
    maxCharsPerLine,
    wrappedLines,
  };
}

function findFittingTypography(
  normalizedContent: string,
  startFontSize: number,
  minFontSize: number,
  maxWidth: number,
  maxHeight: number,
  fontStep: number,
  charWidthRatio: number,
  lineHeightRatio: number
) {
  for (let fontSize = startFontSize; fontSize >= minFontSize; fontSize -= fontStep) {
    const candidate = createTypographyCandidate(
      normalizedContent,
      fontSize,
      maxWidth,
      charWidthRatio,
      lineHeightRatio
    );

    if (candidate.wrappedLines.length * candidate.lineHeight <= maxHeight) {
      return {
        candidate,
        fontSize,
      };
    }
  }

  return null;
}

function truncateToLineBudget(lines: string[], lineBudget: number, maxCharsPerLine: number) {
  const visibleLines = lines.slice(0, lineBudget);

  if (lines.length <= lineBudget || visibleLines.length === 0) {
    return visibleLines;
  }

  const lastIndex = visibleLines.length - 1;
  const ellipsis = "...";
  const truncatedLastLine =
    visibleLines[lastIndex].slice(0, Math.max(0, maxCharsPerLine - ellipsis.length));
  visibleLines[lastIndex] = `${truncatedLastLine}${ellipsis}`;
  return visibleLines;
}

export function fitCodeBlock({
  language,
  content,
  maxWidth,
  maxHeight,
  maxFontSize = 16,
  minFontSize = 8,
  fontStep = DEFAULT_FONT_STEP,
  charWidthRatio = DEFAULT_CODE_CHAR_WIDTH_RATIO,
  lineHeightRatio = DEFAULT_CODE_LINE_HEIGHT_RATIO,
}: FitCodeBlockOptions): FittedCodeBlock {
  const normalizedContent = normalizeCodeContent(language, content);
  const preferredMinFont = Math.max(1, minFontSize);
  const hardMinFont = Math.max(1, Math.min(preferredMinFont, HARD_MIN_FONT_SIZE));
  const startFont = Math.max(maxFontSize, preferredMinFont);

  const preferredFit = findFittingTypography(
    normalizedContent,
    startFont,
    preferredMinFont,
    maxWidth,
    maxHeight,
    fontStep,
    charWidthRatio,
    lineHeightRatio
  );

  if (preferredFit) {
    return {
      text: preferredFit.candidate.wrappedLines.join("\n"),
      fontSize: Math.round(preferredFit.fontSize * 10) / 10,
      lineHeight: preferredFit.candidate.lineHeight,
      fontFamily: DEFAULT_CODE_FONT_FAMILY,
    };
  }

  if (hardMinFont < preferredMinFont) {
    const emergencyFit = findFittingTypography(
      normalizedContent,
      preferredMinFont - fontStep,
      hardMinFont,
      maxWidth,
      maxHeight,
      fontStep,
      charWidthRatio,
      lineHeightRatio
    );

    if (emergencyFit) {
      return {
        text: emergencyFit.candidate.wrappedLines.join("\n"),
        fontSize: Math.round(emergencyFit.fontSize * 10) / 10,
        lineHeight: emergencyFit.candidate.lineHeight,
        fontFamily: DEFAULT_CODE_FONT_FAMILY,
      };
    }
  }

  const fallback = createTypographyCandidate(
    normalizedContent,
    hardMinFont,
    maxWidth,
    charWidthRatio,
    lineHeightRatio
  );
  const fallbackLineBudget = Math.max(1, Math.floor(maxHeight / fallback.lineHeight));
  const fallbackLines = truncateToLineBudget(
    fallback.wrappedLines,
    fallbackLineBudget,
    fallback.maxCharsPerLine
  );

  return {
    text: fallbackLines.join("\n"),
    fontSize: Math.round(hardMinFont * 10) / 10,
    lineHeight: fallback.lineHeight,
    fontFamily: DEFAULT_CODE_FONT_FAMILY,
  };
}
