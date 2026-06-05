import * as z from "zod";



export const slideLayoutId = "introduction-slide";
export const slideLayoutName = "소개 슬라이드";
export const slideLayoutDescription =
  "왼쪽에 큰 인물 이미지가 있고, 오른쪽에 제목과 라벨이 붙은 두 개의 본문 문단으로 구성된 소개 열이 있는 분할 슬라이드.";

const IntroBlockSchema = z.object({
  label: z.string().min(3).max(12).meta({
    description: "Uppercase mini-heading shown above each introduction paragraph.",
  }),
  body: z.string().max(180).meta({
    description: "Supporting paragraph content for the introduction block.",
  }),
});

export const Schema = z.object({
  title: z.string().max(15).default("소개").meta({
    description: "Primary title in the right column.",
  }),
  portraitImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1521119989659-a83eee488004?auto=format&fit=crop&w=1200&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Two business professionals in office"),
  }).optional().default({
    __image_url__:
      "https://images.unsplash.com/photo-1521119989659-a83eee488004?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Two business professionals in office",
  }).meta({
    description: "Main portrait image shown on the left half.",
  }),
  blocks: z
    .array(IntroBlockSchema)

    .max(2)
    .default([
      {
        label: "태그라인",
        body: "예시 본문 내용입니다. 소개 섹션에 들어갈 핵심 메시지를 간결하게 전달하기 위한 자리 표시 문구이며, 실제 내용으로 자유롭게 교체해 사용하실 수 있습니다.",
      },
      {
        label: "태그라인",
        body: "예시 본문 내용입니다. 소개 섹션에 들어갈 핵심 메시지를 간결하게 전달하기 위한 자리 표시 문구이며, 실제 내용으로 자유롭게 교체해 사용하실 수 있습니다.",
      },
    ])
    .meta({
      description: "Two short intro content blocks shown in the text column.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const IntroductionSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, portraitImage, blocks } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#DAE1DE)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className="grid h-full gap-[54px] items-center grid-cols-2">
          <div
            className="h-full w-full overflow-hidden bg-[#15342D]"
            style={{ backgroundColor: "var(--primary-color,#15342D)" }}
          >

            {portraitImage?.__image_url__ && (
              <img
                src={portraitImage.__image_url__}
                alt={portraitImage.__image_prompt__}
                className="h-full w-full object-cover"
              />
            )}
          </div>

          <div className="">
            <h2
              className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {title}
            </h2>

            <div className="mt-[53px] space-y-[53px] pr-[33px]">
              {blocks?.map((block, index) => (
                <div key={`${block.label}-${index}`}>
                  <p
                    className="text-[20px] font-semibold tracking-[2.074px] text-[#15342D]"
                    style={{ color: "var(--primary-color,#15342D)" }}
                  >
                    {block.label}
                  </p>
                  <p
                    className="mt-[14px] text-[24px] leading-[26.667px] text-[#15342DCC]"
                    style={{ color: "var(--background-text,#15342DCC)" }}
                  >
                    {block.body}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default IntroductionSlide;
