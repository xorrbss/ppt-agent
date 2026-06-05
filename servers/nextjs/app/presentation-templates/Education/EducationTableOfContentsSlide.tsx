import * as z from "zod";


export const slideLayoutId = "table-of-contents-slide";
export const slideLayoutName = "목차 슬라이드";
export const slideLayoutDescription =
  "왼쪽 제목 패널과 번호가 매겨진 섹션 목록을 담은 오른쪽 영역으로 구성되며, 은은한 배경 이미지 오버레이가 더해진 분할 레이아웃.";

const TocItemSchema = z.object({
  number: z.string().min(2).max(3).meta({
    description: "Section number displayed before each section title.",
  }),
  label: z.string().min(3).max(30).meta({
    description: "Section title shown in the right column list.",
  }),
});

export const Schema = z.object({
  title: z.string().min(6).max(32).default("목차").meta({
    description: "Main centered title of the table of contents slide.",
  }),
  items: z
    .array(TocItemSchema)
    .min(1)
    .max(10)
    .default([
      { number: "01", label: "소개" },
      { number: "02", label: "타임라인" },
      { number: "03", label: "계열사 소개" },
      { number: "04", label: "서비스" },
      { number: "05", label: "이미지 갤러리" },
      { number: "06", label: "통계" },
      { number: "07", label: "보고서" },
      { number: "08", label: "마무리" },
      { number: "09", label: "질의응답" },
      { number: "10", label: "연락처" },
    ])
    .meta({
      description: "table-of-content entries listed on the right.",
    }),

});

export type SchemaType = z.infer<typeof Schema>;

const EducationTableOfContentsSlide = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <>

      <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden"
        style={{
          backgroundColor: "var(--background-color,#efeff1)",
          fontFamily: "var(--body-font-family,'Source Serif 4')",
        }}
      >


        <div className="relative z-10 grid h-full grid-cols-[430px_1fr]">
          <div className="px-[56px] pt-[74px]" style={{ backgroundColor: "var(--card-color,#f1efef)" }}>
            <h2 className="font-serif text-[64px] leading-[98%] tracking-[-0.02em]" style={{ color: "var(--primary-color,#1a1752)" }}>
              {data.title}
            </h2>
          </div>

          <div className="px-[88px] pt-[84px]" style={{ backgroundColor: "var(--card-color,#FFFFFF80)" }}>
            <div className="space-y-[32px]">
              {data.items?.map((item, index) => (
                <div key={`${item.number}-${item.label}-${index}`} className="flex items-center gap-[16px]">
                  <span className="w-[42px] text-[20px] font-semibold leading-none" style={{ color: "var(--background-text,#3f414a)" }}>
                    {item.number}
                  </span>
                  <span className="text-[24px] font-medium leading-none" style={{ color: "var(--background-text,#3f414a)" }}>
                    {item.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default EducationTableOfContentsSlide;
