import * as z from "zod";


export const slideLayoutId = "content-split-slide";
export const slideLayoutName = "내용 분할 슬라이드";
export const slideLayoutDescription =
  "하나의 반복 이미지로 구성된 왼쪽 콜라주와 제목, 태그라인, 본문 단락이 담긴 오른쪽 내용 블록으로 이루어진 레이아웃.";

export const Schema = z.object({
  heading: z.string().max(24).default("제목").meta({
    description: "Main right-side heading.",
  }),
  tagline: z.string().max(12).default("태그라인").meta({
    description: "Small uppercase label shown under the heading.",
  }),
  body: z.string().max(300).default(
    "저희는 학습자 중심의 교육 철학을 바탕으로 다양한 분야의 전문 과정을 운영하고 있습니다. 체계적인 커리큘럼과 풍부한 실습 기회를 통해 누구나 자신의 속도에 맞춰 성장할 수 있으며, 경험이 풍부한 강사진이 매 순간 곁에서 학습을 이끕니다. 배움의 즐거움과 실질적인 성과를 동시에 누려 보세요."
  ).meta({
    description: "Main descriptive paragraph on the right side.",
  }),
  images: z.array(z.object({
    __image_url__: z.string().default("https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80"),
    __image_prompt__: z.string().default("Business team around a laptop"),
  })).min(1).max(3).default([{
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Business team around a laptop",
  },
  {
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Business team around a laptop",
  },
  {
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Business team around a laptop",
  },
  ]).meta({
    description: "Array of images reused to create the left collage composition.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const EducationContentSplitSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { heading, tagline, body, images } = data;

  return (
    <>

      <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden"
        style={{
          backgroundColor: "var(--background-color,#E6E7E8)",
          fontFamily: "var(--body-font-family,'Source Serif 4')",
        }}
      >
        <div className="w-full flex items-center h-full">
          <div className="w-[660px] h-full">
            <div className="h-[394px] w-full mb-[6px]">
              <img
                src={images?.[0]?.__image_url__}
                alt={images?.[0]?.__image_prompt__}
                className=" h-full w-full object-cover object-center"
              />
            </div>
            <div className="flex w-full gap-[6px] h-[320px] ">
              <div className="w-[330px]"> <img
                src={images?.[1]?.__image_url__}
                alt={images?.[1]?.__image_prompt__}
                className="h-full w-full object-cover "
              /></div>
              <div className="w-[330px]"> <img
                src={images?.[2]?.__image_url__}
                alt={images?.[2]?.__image_prompt__}
                className="h-full w-full object-cover "
              /></div>
            </div>
          </div>

          <div className="w-1/2 px-[56px]">
            <h2 className="text-[24px] font-medium leading-none" style={{ color: "var(--background-text,#34394C)" }}>{heading}</h2>
            <p className=" text-[14px] font-medium mt-1 leading-none tracking-[0.04em]" style={{ color: "var(--background-text,#454962)" }}>
              {tagline}
            </p>
            <p className="mt-[18px] text-[22px] leading-[1.28]" style={{ color: "var(--background-text,#34394C)" }}>{body}</p>
          </div>
        </div>
      </div>
    </>
  );
};

export default EducationContentSplitSlide;
