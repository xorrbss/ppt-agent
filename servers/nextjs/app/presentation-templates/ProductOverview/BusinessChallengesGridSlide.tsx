import * as z from "zod";

export const slideLayoutId = "title-with-blocks-text-slide";
export const slideLayoutName = "제목과 텍스트 블록 그리드 슬라이드";
export const slideLayoutDescription =
  "상단에 제목이 있고, 그 아래 텍스트 블록이 담긴 콘텐츠 섹션이 있는 슬라이드.";

const BlockSchema = z.object({
  heading: z.string().max(30).meta({
    description: "Short heading for a single block of text.",
  }),
  body: z.string().max(80).meta({
    description: "Description text for a single block of text.",
  }),
});

export const Schema = z.object({
  title: z.string().min(8).max(24).default("비즈니스 과제").meta({
    description: "Main title shown in the top.",
  }),
  blocks: z
    .array(BlockSchema)

    .max(4)
    .default([
      {
        heading: "제목 1 제목 1 제목 1 제목 1",
        body: "예시 본문 내용입니다. 핵심 메시지를 간결하게 전달하기 위한 자리 표시 문구입니다.",
      },
      {
        heading: "제목 2",
        body: "예시 본문 내용입니다. 핵심 메시지를 간결하게 전달하기 위한 자리 표시 문구입니다.",
      },
      {
        heading: "제목 1",
        body: "예시 본문 내용입니다. 핵심 메시지를 간결하게 전달하기 위한 자리 표시 문구입니다.",
      },
      {
        heading: "제목 2",
        body: "예시 본문 내용입니다. 핵심 메시지를 간결하게 전달하기 위한 자리 표시 문구입니다.",
      },
    ])
    .meta({
      description: "Four challenge blocks rendered in a 2x2 arrangement.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const BusinessChallengesGridSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, blocks } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden  flex flex-col"
        style={{
          backgroundColor: "var(--background-color,#DAE1DE)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className=" px-[60px] pt-[50px] pb-[28px]">
          <h2
            className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {title}
          </h2>
        </div>

        <div
          className="grid  grid-cols-2 justify-between items-center flex-1 gap-y-[43px] px-[84px] py-[70px] gap-x-[63px]"
          style={{ backgroundColor: "var(--primary-color,#15342D)" }}
        >
          {blocks?.map((block, index) => (
            <div key={index} className="">
              <p
                className="text-[20px] font-semibold tracking-[2.074px] text-white"
                style={{ color: "var(--primary-text,#edf2f1)" }}
              >
                {block.heading}
              </p>
              <p
                className="mt-[24px] text-[28px] font-normal  text-white"
                style={{ color: "var(--primary-text,#edf2f1)" }}
              >
                {block.body}
              </p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default BusinessChallengesGridSlide;
