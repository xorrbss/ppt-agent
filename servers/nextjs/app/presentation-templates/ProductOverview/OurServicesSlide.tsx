import * as z from "zod";


export const slideLayoutId = "title-description-with-image-block-slide";
export const slideLayoutName = "제목·설명과 이미지 블록 슬라이드";
export const slideLayoutDescription =
  "상단에 제목과 그 아래 설명이 있고, 이미지와 텍스트 카드 그리드가 담긴 콘텐츠 섹션이 있는 슬라이드.";

const CardSchema = z.object({
  heading: z.string().max(14).meta({
    description: "Card heading.",
  }),
  body: z.string().max(25).meta({
    description: "Card short description.",
  }),
  isHighlighted: z.boolean().default(false).meta({
    description: "Whether this card uses the dark style.",
  }),
});

export const Schema = z.object({
  title: z.string().max(16).default("서비스 소개").meta({
    description: "Main heading shown at the top-left.",
  }),
  taglineLabel: z.string().max(16).default("태그라인").meta({
    description: "Small label above left paragraph.",
  }),
  taglineBody: z.string().max(30).default(
    "최소한의 노력으로 최고의 결과를 만듭니다."
  ).meta({
    description: "Supporting text shown beneath the tagline label.",
  }),
  featureImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Customer support team in office"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Customer support team in office",
  }).meta({
    description: "Main image shown at the lower left side.",
  }),
  services: z
    .array(CardSchema)

    .max(4)
    .default([
      { heading: "제목 1", body: "예시 서비스 설명입니다.", isHighlighted: false },
      { heading: "제목 2", body: "예시 서비스 설명입니다.", isHighlighted: true },
      { heading: "제목 3", body: "예시 서비스 설명입니다.", isHighlighted: false },
      { heading: "제목 4", body: "예시 서비스 설명입니다.", isHighlighted: false },
    ])
    .meta({
      description: "Cards rendered on the right side.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const OurServicesSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, taglineLabel, taglineBody, featureImage, services } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] flex items-end pb-[56px]  justify-between overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#DAE1DE)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className=" pt-[50px]">
          <div className="px-[68px]">

            <h2
              className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {title}
            </h2>

            <div className="mt-[26px] w-[560px]">
              <p
                className="text-[20px] font-semibold tracking-[2.074px] text-white"
                style={{ color: "var(--primary-color,#15342D)" }}
              >
                {taglineLabel}
              </p>
              <p
                className="mt-[14px] text-[24px] font-normal  text-[#15342DCC]"
                style={{ color: "var(--background-text,#15342DCC)" }}
              >
                {taglineBody}
              </p>
            </div>
          </div>
          <div
            className="mt-[35px] h-[326px] w-[650px] bg-[#15342D]"
            style={{ backgroundColor: "var(--primary-color,#15342D)" }}
          >

            {featureImage?.__image_url__ && (
              <img
                src={featureImage?.__image_url__}
                alt={featureImage?.__image_prompt__}
                className="h-[326px] w-[650px] object-cover"
              />
            )}
          </div>
        </div>



        <div className="grid grid-cols-2 gap-[22px] pr-[76px]">
          {services?.map((card, index) => (
            <div
              key={index}
              className=" p-[33px]"
              style={{
                backgroundColor: card.isHighlighted
                  ? "var(--primary-color,#15342D)"
                  : "var(--card-color,#ececee)",
              }}
            >
              <p
                className="text-[20px] font-semibold tracking-[4.354px] text-white"
                style={{
                  color: card.isHighlighted
                    ? "var(--primary-text,#edf2f1)"
                    : "var(--primary-color,#15342D)",
                }}
              >
                {card.heading}
              </p>
              <p
                className={`${card.isHighlighted ? "text-white" : "text-[#15342D]"} mt-[20px] text-[28px] font-normal`}
                style={{
                  color: card.isHighlighted
                    ? "var(--primary-text,#edf2f1)"
                    : "var(--background-text,#15342DCC)",
                }}
              >
                {card.body}
              </p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default OurServicesSlide;
