import * as z from "zod";


export const slideLayoutId = "services-split-slide";
export const slideLayoutName = "서비스 분할 슬라이드";
export const slideLayoutDescription =
  "제목이 담긴 왼쪽 텍스트 열, 하나의 이미지 열, 그리고 오른쪽에 세로로 쌓인 서비스 설명 블록으로 구성된 레이아웃.";

const ServiceSchema = z.object({
  image: z.object({
    __image_url__: z.string(),
    __image_prompt__: z.string(),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Team meeting image reused across two rows",
  }).meta({
    description: "Single image in the middle column.",
  }),
  heading: z.string().min(3).max(18).meta({
    description: "Heading shown in the right column.",
  }),
  tagline: z.string().min(3).max(12).meta({
    description: "Short label under each  heading.",
  }),
  body: z.string().max(40).meta({
    description: "Description paragraph shown below the heading and tagline.",
  }),
});

export const Schema = z.object({
  title: z.string().max(16).default("서비스").meta({
    description: "Main slide title shown on the left.",
  }),
  sections: z
    .array(ServiceSchema)
    .min(1)
    .max(4)
    .default([
      {
        image: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "서비스 1",
        tagline: "태그라인",
        body: "맞춤형 정규 교육 과정을 제공합니다.",
      },
      {
        image: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "서비스 2",
        tagline: "태그라인",
        body: "실무 중심의 전문 실습 과정입니다.",
      },
      {
        image: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "서비스 3",
        tagline: "태그라인",
        body: "일대일 학습 상담과 진로 지원입니다.",
      },
      {
        image: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "서비스 4",
        tagline: "태그라인",
        body: "온라인 학습 플랫폼을 운영합니다.",
      },
    ])
    .meta({
      description: "Two stacked service content sections on the right side.",
    }),

});

export type SchemaType = z.infer<typeof Schema>;

const EducationServicesSplitSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, sections } = data;


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
        <div className="grid h-full grid-cols-[365px_1fr]">
          <div className="px-[53px] pt-[53px]">
            <h2 className="font-serif text-[64px] leading-[98%] tracking-[-0.02em]" style={{ color: "var(--primary-color,#1a1752)" }}>
              {title}
            </h2>
          </div>



          <div className="  grid  "
            style={{
              gridTemplateRows: `repeat(${sections?.length}, 1fr)`,
            }}
          >
            {sections?.map((section, index) => (
              <div key={`${section.heading}-${index}`} className=" flex items-center"
                style={{
                  borderBottom:
                    index !== (sections?.length ?? 1) - 1
                      ? "5px solid var(--stroke,rgba(255, 255, 255, 0.10))"
                      : "none",
                }}
              >
                <div className=" min-w-[316px] max-w-[316px] "
                  style={{
                    height: sections?.length === 4 ? '175px' : sections?.length === 3 ? '240px' : '357px'
                  }}
                >

                  <img
                    src={section.image?.__image_url__}
                    alt={section.image?.__image_prompt__}
                    className="h-full w-full object-cover "
                  />
                </div>
                <div
                  className={`px-[56px] `}
                >
                  <h3 className="text-[24px] font-medium leading-none" style={{ color: "var(--background-text,#34394C)" }}>{section.heading}</h3>
                  <p className="mt-[10px] text-[14px] font-medium uppercase leading-none" style={{ color: "var(--background-text,#454962)" }}>
                    {section.tagline}
                  </p>
                  <p className="mt-[18px]  text-[22px] leading-[1.26] tracking-[0.04em]" style={{ color: "var(--background-text,#34394C)" }}>
                    {section.body}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
};

export default EducationServicesSplitSlide;
