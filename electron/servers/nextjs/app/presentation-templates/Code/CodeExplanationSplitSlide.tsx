import * as z from "zod";

const CODE_BLOCK_MAX_FONT_SIZE = 16;
const CODE_BLOCK_MIN_FONT_SIZE = 8;
const CODE_BLOCK_WIDTH = 506;
const CODE_BLOCK_HEIGHT = 430;
const CODE_CHAR_WIDTH_RATIO = 0.62;
const CODE_LINE_HEIGHT_RATIO = 1.25;
const CODE_FONT_FAMILY = "var(--code-font-family,'Liberation Mono', monospace)";

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

function normalizeCodeContent(language?: string, content?: string) {
  let normalizedContent = (content || "")
    .replace(/\r\n?/g, "\n")
    .replace(/\\\[/g, "[")
    .replace(/\\\]/g, "]")
    .trimEnd();

  if (language?.toLowerCase() === "python") {
    normalizedContent = normalizePythonCode(normalizedContent);
  }

  return normalizedContent;
}

function getCodeBlockTypography(content?: string) {
  const normalizedLines = (content || "").replace(/\t/g, "  ").split("\n");
  const longestLineLength = Math.max(
    1,
    ...normalizedLines.map((line) => line.length)
  );

  for (let fontSize = CODE_BLOCK_MAX_FONT_SIZE; fontSize >= CODE_BLOCK_MIN_FONT_SIZE; fontSize -= 0.5) {
    const lineHeight = Math.round(fontSize * CODE_LINE_HEIGHT_RATIO);
    const fitsWidth = longestLineLength * fontSize * CODE_CHAR_WIDTH_RATIO <= CODE_BLOCK_WIDTH;
    const fitsHeight = normalizedLines.length * lineHeight <= CODE_BLOCK_HEIGHT;

    if (fitsWidth && fitsHeight) {
      return { fontSize, lineHeight };
    }
  }

  return {
    fontSize: CODE_BLOCK_MIN_FONT_SIZE,
    lineHeight: Math.round(CODE_BLOCK_MIN_FONT_SIZE * CODE_LINE_HEIGHT_RATIO),
  };
}

function getCodeLineRuns(content: string, lineHeight: number) {
  const codeLineRuns: { text: string; marginTop: number }[] = [];
  let blankLineCount = 0;

  for (const line of content.split("\n")) {
    if (line.length === 0) {
      blankLineCount += 1;
      continue;
    }

    codeLineRuns.push({
      text: line,
      marginTop: blankLineCount * lineHeight,
    });
    blankLineCount = 0;
  }

  return codeLineRuns;
}

export const slideLayoutId = "code-explanation-split-slide";
export const slideLayoutName = "Code Explanation Split Slide";
export const slideLayoutDescription =
  "A two-column slide with a code panel on the left and description on the right.";

export const Schema = z.object({
  title: z.string().min(8).max(24).default("Code + Explanation").meta({
    description: "Slide heading shown at the top-left.",
  }),
  codeSnippet: z.object({
    language: z.string().min(2).max(10).meta({
      description: "Programming language of the snippet",
    }),
    fileName: z.string().min(3).max(30).meta({
      description: "File name label shown above the code snippet.",
    }),
    content: z.string().min(20).max(520).meta({
      description: "The actual code content to be displayed.",
    }),
  }).default({
    language: "tsx",
    fileName: "components/UserAuth.tsx",
    content: `import { useState } from "react";
import { login } from "@/lib/auth";

export function UserAuth() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const user = await login(email, password);
    console.log("Logged in:", user);
  };

  return null;
}
  
`,
  }).meta({
    description: "Code sample shown in the left panel.",
  }),
  descriptionTitle: z.string().min(4).max(20).default("Description").meta({
    description: "Heading shown above the paragraph.",
  }),
  description: z
    .string()
    .min(40)
    .max(360)
    .default(
      "This component manages credentials as local state and submits them through an async handler. The login utility abstracts network details while the handler keeps the UI flow predictable. Keep validation and side effects isolated so changes remain safe when authentication requirements evolve. "
    )
    .meta({
      description: "Description paragraph shown in the right column.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide02CodeExplanationSplit = ({
  data,
}: {
  data: Partial<SchemaType>;
}) => {
  const normalizedCodeContent = normalizeCodeContent(
    data.codeSnippet?.language,
    data.codeSnippet?.content
  );
  const codeTypography = getCodeBlockTypography(normalizedCodeContent);
  const codeLineRuns = getCodeLineRuns(normalizedCodeContent, codeTypography.lineHeight);

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:ital,opsz,wght@0,6..12,200..1000;1,6..12,200..1000&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden p-[53px]"
        style={{
          backgroundColor: "var(--background-color,#101B37)",
          fontFamily: "var(--body-font-family,Nunito Sans)",
        }}
      >

        <div className="relative z-10 flex h-full flex-col">
          <h2 className="text-[64px] font-medium" style={{ color: "var(--background-text,#ffffff)" }}>{data.title}</h2>

          <div className="mt-[22px] grid min-h-0  flex-1 grid-cols-2 gap-[34px]">
            <div
              className="flex h-full min-h-0 flex-col overflow-hidden rounded-[18px] border"
              style={{
                backgroundColor: "var(--card-color,#0F172B80)",
                borderColor: "var(--stroke,#1D293D80)",
              }}
            >
              <p
                className="text-[18px] capitalize rounded-t-[18px] border px-[26px] py-3"
                style={{
                  color: "var(--background-text,#CAD5E2)",
                  backgroundColor: "var(--card-color,#0F172BCC)",
                  borderColor: "var(--stroke,#1D293D80)",
                }}
              >
                {data.codeSnippet?.fileName}
              </p>
              <div
                className="min-h-0 w-full flex-1 overflow-hidden px-[32px] py-[20px]"
                style={{
                  color: "var(--background-text,#ffffff)",
                  fontFamily: CODE_FONT_FAMILY,
                }}
              >
                {codeLineRuns.map((codeLineRun, index) => (
                  <div
                    key={`code-line-${index}`}
                    style={{
                      marginTop: codeLineRun.marginTop ? `${codeLineRun.marginTop}px` : undefined,
                      fontSize: `${codeTypography.fontSize}px`,
                      lineHeight: `${codeTypography.lineHeight}px`,
                      whiteSpace: "pre",
                    }}
                  >
                    {codeLineRun.text}
                  </div>
                ))}
              </div>
            </div>

            <div className=" ">
              <h3 className="text-[24px] font-medium" style={{ color: "var(--background-text,#f1f4ff)" }}>{data.descriptionTitle}</h3>
              <p className="mt-[18px] text-[22px] leading-[145%]" style={{ color: "var(--background-text,#d2d9ff)" }}>
                {data.description}
              </p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default CodeSlide02CodeExplanationSplit;
