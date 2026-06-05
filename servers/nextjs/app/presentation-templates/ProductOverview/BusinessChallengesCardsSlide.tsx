import * as z from "zod";



export const slideLayoutId = "title-description-with-cards-text-slide";
export const slideLayoutName = "제목·설명과 텍스트 카드 슬라이드";
export const slideLayoutDescription =
  "상단에 제목과 그 아래 설명이 있고, 텍스트 카드가 담긴 콘텐츠 섹션이 있는 슬라이드.";

const CardSchema = z.object({
  heading: z.string().max(16).meta({
    description: "Card heading for one challenge column.",
  }),
  body: z.string().max(45).meta({
    description: "Card body copy for one challenge column.",
  }),
  dark: z.boolean().default(false).meta({
    description: "Controls whether the card uses a dark emphasis style.",
  }),
});

export const Schema = z.object({
  title: z.string().min(8).max(16).default("비즈니스 과제").meta({
    description: "Main slide title. Max 16 characters.",
  }),
  taglineLabel: z.string().max(16).default("태그라인").meta({
    description: "Short label above the left-side paragraph.",
  }),
  taglineBody: z.string().max(100).default(
    "최소한의 노력으로 최대의 성과를 내기 위해 팀 전체가 함께 고민하고 실행해 나가고 있습니다."
  ).meta({
    description: "Supporting paragraph on the left side.",
  }),
  heroImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1552664730-d307ca884978?auto=format&fit=crop&w=1400&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Team meeting and stressed analyst"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1552664730-d307ca884978?auto=format&fit=crop&w=1400&q=80",
    __image_prompt__: "Team meeting and stressed analyst",
  }).meta({
    description: "Primary image shown in the upper right area.",
  }),
  cards: z
    .array(CardSchema)

    .max(3)
    .default([
      {
        heading: "제목 1",
        body: "핵심 과제를 간결하게 설명하는 예시 문구입니다.",
        dark: false,
      },
      {
        heading: "제목 2",
        body: "핵심 과제를 간결하게 설명하는 예시 문구입니다.",
        dark: false,
      },
      {
        heading: "제목 3",
        body: "핵심 과제를 간결하게 설명하는 예시 문구입니다.",
        dark: true,
      },
    ])
    .meta({
      description: "Three vertical challenge cards rendered under the image.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const BusinessChallengesCardsSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, taglineLabel, taglineBody, heroImage, cards } = data;

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
        <div className=" pl-[66px] pt-[50px] pb-[28px] pr-[40px]">
          <h2
            className="text-[80px] max-w-[406px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {title}
          </h2>

          <div className="mt-[72px] w-[360px]">
            <p
              className="text-[20px] font-semibold tracking-[2.074px] text-white"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {taglineLabel}
            </p>
            <p
              className="mt-[16px] text-[24px] font-normal  text-[#15342DCC]"
              style={{ color: "var(--background-text,#15342DCC)" }}
            >
              {taglineBody}
            </p>
          </div>
        </div>

        {heroImage?.__image_url__ && (
          <img
            src={heroImage.__image_url__}
            alt={heroImage.__image_prompt__}
            className="absolute right-0 top-[72px] h-[350px] w-[770px] object-cover bg-white"
          />
        )}

        <div className="absolute bottom-[72px] right-[40px] flex items-start gap-[16px]">
          {cards?.map((card, index) => (
            <div
              key={index}
              className=" w-[248px] px-[34px] py-[34px]"
              style={{
                backgroundColor: card.dark
                  ? "var(--primary-color,#15342D)"
                  : "var(--card-color,#ebebee)",
              }}
            >
              <p
                className="text-[20px] font-semibold tracking-[2.074px] text-white"
                style={{
                  color: card.dark
                    ? "var(--primary-text,#edf2f1)"
                    : "var(--primary-color,#15342D)",
                }}
              >
                {card.heading}
              </p>
              <p
                className="mt-[18px] text-[28px] font-normal  text-white"
                style={{
                  color: card.dark
                    ? "var(--primary-text,#edf2f1)"
                    : "var(--primary-color,#15342D)",
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

export default BusinessChallengesCardsSlide;
