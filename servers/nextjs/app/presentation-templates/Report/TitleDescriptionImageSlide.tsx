import * as z from "zod";


export const slideLayoutId = "title-description-image-slide";
export const slideLayoutName = "제목·설명·이미지 슬라이드";
export const slideLayoutDescription =
  "왼쪽 상단에 제목이 있고, 제목 아래에 문단 블록, 슬라이드 오른쪽에 큰 보조 이미지가 고정 배치된 슬라이드.";

export const Schema = z.object({
  title: z.string().min(3).max(12).default("소개").meta({
    description: "Title/heading of the slide",
  }),
  body: z.string().max(250).default(
    "여기에 본문 내용을 입력하세요. 슬라이드의 핵심 메시지를 설명하는 문단으로, 데이터의 의미와 시사점을 간결하게 정리하여 청중이 한눈에 이해할 수 있도록 작성합니다. 발표 주제에 맞게 자유롭게 수정할 수 있습니다."
  ).meta({
    description: "Primary paragraph shown under the title.",
  }),
  bullets: z
    .array(z.string().max(100))
    .min(0)
    .max(4)
    .optional()
    .default([
      "여기에 핵심 항목을 입력하세요",
      "주요 내용을 간결하게 작성하세요",
      "데이터의 의미를 한 줄로 정리하세요",
      "발표 주제에 맞게 자유롭게 수정하세요",
    ])
    .meta({
      description: "Optional bullet list shown after the description if required.",
    }),
  featureImage: z.object({
    __image_url__: z.string(),
    __image_prompt__: z.string(),
  }).optional().meta({
    description: "Large image shown on the right side of the slide or optional.",
  }).default({
    __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
    __image_prompt__: "Thoughtful woman portrait on a neutral backdrop",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const IntroductionImageSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, body, bullets, featureImage } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,200..900;1,200..900&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#f9f8f8]"
        style={{
          backgroundColor: "var(--background-color,#f9f8f8)",
          fontFamily: "var(--body-font-family,'Source Sans 3')",
        }}
      >
        <div
          className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
          style={{ height: 185, backgroundColor: "var(--primary-color,#157CFF)" }}
        />

        <div className="px-[74px] pt-[76px]">
          <h2
            className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {title}
          </h2>
        </div>

        <div className="flex gap-28 pl-[96px] pt-[30px]">
          <div className="flex  flex-col">
            <p className=" text-[24px] leading-[26.667px] text-[#232223]" style={{ color: "var(--background-text,#232223)" }}>
              {body}
            </p>

            <div
              className="mt-8 list-disc pl-[28px] text-[24px] leading-[26.667px] text-[#232223]"
              style={{ color: "var(--background-text,#232223)" }}
            >
              {bullets?.map((bullet, index) => (
                <div key={`${bullet}-${index}`} className="mt-[8px] flex items-center gap-2">
                  <div className="w-[8px] h-[8px] rounded-full bg-[#232223]" style={{ backgroundColor: "var(--background-text,#232223)" }} /> <p className="text-[24px] leading-[26.667px] text-[#232223]" style={{ color: "var(--background-text,#232223)" }}>
                    {bullet}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-1 items-end justify-end">
            <div
              className="h-[397px] w-[582px] overflow-hidden rounded-l-[106px] bg-[#157CFF]"
              style={{ backgroundColor: "var(--primary-color,#157CFF)" }}
            >
              <img
                src={featureImage?.__image_url__}
                alt={featureImage?.__image_prompt__}
                className="h-full w-full object-cover"
              />
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default IntroductionImageSlide;
