import * as z from "zod";


export const slideLayoutId = "code-explanation-split-slide";
export const slideLayoutName = "Code Explanation Split Slide";
export const slideLayoutDescription =
  "A two-column slide with a code panel on the left and descriptive explanation on the right.";

export const Schema = z.object({
  title: z.string().min(8).max(24).default("Code + Explanation").meta({
    description: "Slide heading shown at the top-left.",
  }),
  codeSnippet: z.object({
    language: z.string().min(2).max(10),
    fileName: z.string().min(3).max(30),
    content: z.string().min(20).max(520),
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
  explanationTitle: z.string().min(4).max(20).default("Explanation").meta({
    description: "Heading shown above the explanatory paragraph.",
  }),
  explanation: z
    .string()
    .min(40)
    .max(360)
    .default(
      "This component manages credentials as local state and submits them through an async handler. The login utility abstracts network details while the handler keeps the UI flow predictable. Keep validation and side effects isolated so changes remain safe when authentication requirements evolve. "
    )
    .meta({
      description: "Explanation paragraph shown in the right column.",
    }),
  pageLabel: z.string().min(3).max(8).default("2 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide02CodeExplanationSplit = ({
  data,
}: {
  data: Partial<SchemaType>;
}) => {

  return (
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
            className=" flex-1 border rounded-[18px]"
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
            <pre className=" w-full px-[32px] py-[20px] whitespace-pre-wrap break-words overflow-hidden" style={{ color: "var(--background-text,#ffffff)" }}>

              <code className="w-full ">
                {data.codeSnippet?.content}
              </code>
            </pre>
          </div>

          <div className=" ">
            <h3 className="text-[24px] font-medium" style={{ color: "var(--background-text,#f1f4ff)" }}>{data.explanationTitle}</h3>
            <p className="mt-[18px] text-[22px] leading-[145%]" style={{ color: "var(--background-text,#d2d9ff)" }}>
              {data.explanation}
            </p>
          </div>
        </div>
      </div>

      <div
        className="absolute bottom-[26px] z-50 left-1/2 -translate-x-1/2 rounded-full border px-[22px] py-[8px] text-[14px]"
        style={{
          borderColor: "var(--stroke,#31415880)",
          backgroundColor: "var(--card-color,#1D293DCC)",
          color: "var(--background-text,#CAD5E2)",
        }}
      >
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide02CodeExplanationSplit;
