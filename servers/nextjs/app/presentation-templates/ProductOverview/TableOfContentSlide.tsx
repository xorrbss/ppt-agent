import * as z from "zod";

const PRODUCT_BG = "var(--background-color,#d7dddd)";
const PRODUCT_DARK = "var(--primary-color,#05463d)";


export const slideLayoutId = "table-of-content-slide";
export const slideLayoutName = "목차 슬라이드";
export const slideLayoutDescription =
  "왼쪽 패널에 섹션 제목과 번호가 있고, 오른쪽 패널에 제목과 설명 문단이 있는 2열 목차 슬라이드.";

const SectionSchema = z.object({
  title: z.string().min(4).max(25).meta({
    description: "Section label shown in the left navigation column.",
  }),
  number: z.string().min(2).max(3).meta({
    description: "Section number shown beside the section label.",
  }),
  description: z.string().min(4).max(60).optional().meta({
    description: "Section description shown in the right column.",
  }),
});

export const Schema = z.object({
  title: z.string().min(6).max(18).default("목차").meta({
    description: "Heading in the right-side content area.",
  }),
  description: z.string().min(50).max(160).default(
    "예시 설명 문구입니다. 이번 발표에서 다룰 주요 섹션을 한눈에 안내하기 위한 자리 표시 텍스트로, 실제 내용으로 자유롭게 교체해 사용하실 수 있습니다."
  ).meta({
    description: "Supporting descriptive paragraph under the heading.",
  }),
  sections: z
    .array(SectionSchema)
    .max(6)
    .default([
      { title: "섹션 제목 섹션 제목", number: "01", description: "예시 섹션 설명입니다. 예시 섹션 설명입니다." },
      { title: "섹션 제목 섹션 제목", number: "02", description: "예시 섹션 설명입니다. 예시 섹션 설명입니다." },
      { title: "섹션 제목 섹션 제목", number: "03", description: "예시 섹션 설명입니다. 예시 섹션 설명입니다." },
      { title: "섹션 제목 섹션 제목", number: "04", description: "예시 섹션 설명입니다. 예시 섹션 설명입니다." },
      { title: "섹션 제목 섹션 제목", number: "05", description: "예시 섹션 설명입니다. 예시 섹션 설명입니다." },
      { title: "섹션 제목 섹션 제목", number: "06", description: "예시 섹션 설명입니다. 예시 섹션 설명입니다." },

    ])
    .meta({
      description: "Six rows listed in the table of contents panel.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const TableOfContentSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, description, sections } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden "
        style={{
          backgroundColor: PRODUCT_BG,
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className="grid h-full grid-cols-[1fr_1fr]">
          <div className="px-[56px] pt-[50px]" style={{ backgroundColor: PRODUCT_DARK }}>
            <div className={`${sections && sections?.length > 3 ? 'space-y-[28px]' : 'space-y-[40px]'}`}>
              {sections?.map((section, index) => (
                <div key={index} className="flex items-center gap-4 justify-between">
                  <div>

                    <p
                      className="text-[20px] font-semibold  tracking-[0.2em] text-[#ecf2f1]"
                      style={{ color: "var(--primary-text,#ecf2f1)" }}
                    >
                      {section.title}
                    </p>
                    {section.description && <p
                      className="mt-[6px] text-[18px] leading-[1.2] text-[#ecf2f1]"
                      style={{ color: "var(--primary-text,#ecf2f1)" }}
                    >
                      {section.description}
                    </p>}
                  </div>
                  <p
                    className="text-[22px] font-medium text-[#ecf2f1]"
                    style={{ color: "var(--primary-text,#ecf2f1)" }}
                  >
                    {section.number}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="px-[42px] pt-[118px]">
            <h2
              className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {title}
            </h2>
            <p
              className="mt-[28px] w-[560px] text-[24px] font-normal  text-[#15342DCC]"
              style={{ color: "var(--background-text,#15342DCC)" }}
            >
              {description}
            </p>
          </div>
        </div>
      </div>
    </>
  );
};

export default TableOfContentSlide;
