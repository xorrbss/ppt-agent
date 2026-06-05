import * as z from "zod";

export const slideLayoutId = "description-text-slide";
export const slideLayoutName = "설명 텍스트 슬라이드";
export const slideLayoutDescription =
  "제목/헤딩이 있는 텍스트 전용 설명 슬라이드.";

export const Schema = z.object({
  title: z.string().min(8).max(30).default("코드 + 설명").meta({
    description: "Main slide title shown at the top-left.",
  }),
  descriptionTitle: z.string().min(4).max(20).default("설명").meta({
    description: "Subheading above the paragraph body.",
  }),
  description: z
    .string()

    .max(360)
    .default(
      "문장은 짧고 명확하게 작성하세요. 핵심 메시지를 먼저 제시하고 이를 뒷받침하는 근거와 예시를 덧붙이면 내용이 한층 설득력 있게 전달됩니다. 불필요한 수식어는 줄이고 핵심에 집중하세요."
    )
    .meta({
      description: "Long-form explanation body.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide08CodeExplanationText = ({ data }: { data: Partial<SchemaType> }) => {

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


        <h2 className="text-[64px] font-medium" style={{ color: "var(--background-text,#f2f4ff)" }}>{data.title}</h2>
        <div className="relative z-10 h-full max-w-[560px]">
          <h3 className="mt-[34px] text-[24px] font-medium" style={{ color: "var(--background-text,#f1f4ff)" }}>{data.descriptionTitle}</h3>
          <p className="mt-[16px] text-[22px] leading-[145%]" style={{ color: "var(--background-text,#d2d9ff)" }}>{data.description}</p>
        </div>
      </div>
    </>
  );
};

export default CodeSlide08CodeExplanationText;
