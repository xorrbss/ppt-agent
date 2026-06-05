import * as z from "zod";


export const slideLayoutId = "about-slide";
export const slideLayoutName = "소개 슬라이드";
export const slideLayoutDescription =
  "회사/강사/발표자/기관 이름과 제목 소개가 담긴 왼쪽 텍스트 열, 그리고 하나의 반복 이미지와 색조 텍스트 패널로 구성된 오른쪽 시각 그리드로 이루어진 레이아웃.";

export const Schema = z.object({
  name: z.string().min(3).max(22).default("회사 이름").meta({
    description: "Main heading in the left content column.",
  }),
  intro: z.string().min(40).max(100).default(
    "저희는 모든 학습자의 잠재력을 끌어내어 더 나은 미래를 함께 만들어 가는 교육 기관입니다."
  ).meta({
    description: "Bold intro text shown beneath the company heading.",
  }),
  body: z.string().min(120).max(280).default(
    "저희는 다양한 교육 프로그램과 체계적인 커리큘럼을 통해 학습자가 자신의 목표를 이룰 수 있도록 지원합니다. 풍부한 경험을 갖춘 전문 강사진이 개개인의 수준에 맞춘 학습 환경을 제공하며, 이론과 실습을 균형 있게 결합하여 실질적인 역량을 키울 수 있도록 돕습니다. 또한 지속적인 피드백과 상담을 통해 학습자가 끝까지 성장하도록 든든한 동반자가 되겠습니다."
  ).meta({
    description: "Body paragraph in the left content section.",
  }),
  topPanelText: z.string().min(20).max(70).default("회사에 대한 정보를 입력하세요.").meta({
    description: "Short text inside the top-right dark panel. ",
  }),
  bottomPanelText: z.string().min(20).max(70).default("회사와 미션에 대한 정보를 입력하세요.").meta({
    description: "Short text inside the bottom-right dark panel.",
  }),
  topFeatureImage: z.object({
    __image_url__: z.string().default("https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80"),
    __image_prompt__: z.string().default("Office team collaboration"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Office team collaboration",
  }).meta({
    description: "Single image reused in the top right-side visual grid.",
  }),
  bottomFeatureImage: z.object({
    __image_url__: z.string().default("https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80"),
    __image_prompt__: z.string().default("Office team collaboration"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Office team collaboration",
  }).meta({
    description: "Single image reused in the bottom right-side visual grid.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const EducationAboutSlide = ({ data }: { data: Partial<SchemaType> }) => {


  return (<>
    <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900&display=swap" rel="stylesheet" />

    <div
      className="relative h-[720px] w-[1280px] overflow-hidden"
      style={{
        backgroundColor: "var(--background-color,#efeff1)",
        fontFamily: "var(--body-font-family,'Source Serif 4')",
      }}
    >
      <div className="grid  items-end grid-cols-[1fr_1fr]">
        <div className="px-[53px] pb-[56px] ">
          <h2 className="font-serif text-[64px] leading-[98%] tracking-[-0.02em]" style={{ color: "var(--primary-color,#101C3D)" }}>
            {data.name}
          </h2>
          <p className="mt-[30px] max-w-[610px] text-[22px] font-semibold leading-[1.24]" style={{ color: "var(--background-text,#34394C)" }}>
            {data.intro}
          </p>
          <p className="mt-[18px] max-w-[620px] text-[22px] leading-[1.28]" style={{ color: "var(--background-text,#46474C)" }}>
            {data.body}
          </p>
        </div>

        <div className=" ">
          <div className="relative flex  overflow-hidden  h-[360px]">
            <img
              src={data.topFeatureImage?.__image_url__}
              alt={data.topFeatureImage?.__image_prompt__}
              className="absolute inset-0 h-full w-full object-cover z-1 "
            />
            <div className="w-1/2 z-10 flex justify-center items-center relative">
              <div
                className="absolute inset-0"
                style={{
                  backgroundColor: "var(--primary-color,#28256f)",
                  opacity: 0.6,
                }}
              />
              <p className="relative z-10 text-[24px] leading-[1.22] px-[42px]" style={{ color: "var(--primary-text,#f5f7ff)" }}>
                {data.topPanelText}
              </p>
            </div>
            <div className=" w-1/2 ">

            </div>
          </div>

          <div className="relative flex overflow-hidden  h-[360px]">
            <img
              src={data.bottomFeatureImage?.__image_url__}
              alt={data.bottomFeatureImage?.__image_prompt__}
              className="absolute inset-0 h-full w-full object-cover "
            />
            <div className=" w-1/2 ">

            </div>
            <div className="w-1/2 z-10 flex justify-center items-center relative">
              <div
                className="absolute inset-0"
                style={{
                  backgroundColor: "var(--primary-color,#28256f)",
                  opacity: 0.6,
                }}
              />
              <p className="relative z-10 text-[24px] leading-[1.22] px-[42px]" style={{ color: "var(--primary-text,#f5f7ff)" }}>
                {data.bottomPanelText}
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </>
  );
};

export default EducationAboutSlide;
