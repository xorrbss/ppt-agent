import * as z from "zod";

export const slideLayoutId = "title-description-with-lines-and-circles-slide";
export const slideLayoutName = "제목·설명과 선·원형 슬라이드";
export const slideLayoutDescription =
  "상단에 제목과 그 아래 설명이 있고, 글머리 목록과 원형 그리드가 담긴 콘텐츠 섹션이 있는 텍스트 슬라이드.";

const BulletSchema = z.object({
  text: z.string().min(12).max(46).meta({
    description: "Bullet text shown on the left side of a line.",
  }),
});

export const Schema = z.object({
  title: z.string().max(18).default("시장 기회").meta({
    description: "Main heading shown at the top-left.",
  }),
  subtitle: z.string().min(40).max(110).default(
    "예시 설명 문구입니다. 시장 기회의 핵심을 간결하게 전달하기 위한 자리 표시 텍스트로, 실제 내용으로 자유롭게 교체해 사용하실 수 있습니다."
  ).meta({
    description: "Supporting text under the main heading.",
  }),
  bullets: z
    .array(BulletSchema)
    .min(4)
    .max(4)
    .default([
      { text: "예시 글머리 항목 내용입니다." },
      { text: "예시 글머리 항목 내용입니다." },
      { text: "예시 글머리 항목 내용입니다." },
      { text: "예시 글머리 항목 내용입니다." },
    ])
    .meta({
      description: "Four bullet-line entries shown on the left.",
    }),
  values: z
    .array(z.string().min(2).max(6))
    .min(4)
    .max(4)
    .default(["$33", "$20", "$120", "$200"])
    .meta({
      description: "Four values shown from outer to inner circles.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const COLORS = [
  "var(--graph-0,#5f7f79)",
  "var(--graph-1,#1f5a4f)",
  "var(--graph-2,#0d4f43)",
  "var(--graph-3,#06463d)",
];

const MarketOpportunitySlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, subtitle, bullets, values } = data;

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
        <div className="px-[56px] pt-[50px]">
          <h2
            className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {title}
          </h2>
          <p
            className="mt-[20px] w-[730px] text-[24px] font-normal  text-[#15342DCC]"
            style={{ color: "var(--background-text,#15342DCC)" }}
          >
            {subtitle}
          </p>
        </div>

        <div className="absolute left-[56px] top-[368px] space-y-[42px]">
          {bullets?.map((bullet, index) => (
            <div key={index} className="relative flex items-center">
              <span
                className="mr-[14px] h-[14px] w-[14px] rounded-full bg-[#0a4a3f]"
                style={{ backgroundColor: "var(--graph-0,#0a4a3f)" }}
              />
              <p
                className="w-[640px] text-[24px] font-normal  text-[#15342DCC]"
                style={{ color: "var(--background-text,#15342DCC)" }}
              >
                {bullet.text}
              </p>
              <span
                className="ml-[8px] h-[2px] w-[80px] bg-[#8ea8a5]"
                style={{ backgroundColor: "var(--stroke,#8ea8a5)" }}
              />
              <span
                className="h-[6px] w-[6px] rounded-full bg-[#edf2f1]"
                style={{ backgroundColor: "var(--primary-text,#edf2f1)" }}
              />
            </div>
          ))}
        </div>

        <div className="absolute bottom-[58px] right-[48px] h-[474px] w-[474px]">
          {values?.map((value, index) => (
            <div
              key={index}
              className="absolute rounded-full"
              style={{
                width: 237 + index * 50,
                height: 237 + index * 50,
                bottom: 0,
                right: 0,
                backgroundColor: COLORS[index],
              }}
            >
              <p
                className="pt-[24px] text-center text-[24px] font-normal  text-white"
                style={{ color: "var(--primary-text,#ffffff)" }}
              >
                {value}
              </p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default MarketOpportunitySlide;
