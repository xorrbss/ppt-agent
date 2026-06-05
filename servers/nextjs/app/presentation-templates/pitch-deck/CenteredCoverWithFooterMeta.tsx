import * as z from "zod";

export const slideLayoutId = "centered-cover-with-footer-meta";
export const slideLayoutName = "하단 메타데이터가 있는 중앙 표지";
export const slideLayoutDescription =
  "중앙 정렬된 제목, 부제목, 하단 메타데이터 그룹으로 구성된 단일 초점 표지 레이아웃입니다.";

const FooterMetaSchema = z.object({
  label: z.string().max(14).meta({
    description: "Footer metadata label.",
  }),
  value: z.string().max(24).meta({
    description: "Footer metadata value.",
  }),
});

export const Schema = z.object({
  title: z.string().max(20).default("프레젠테이션").meta({
    description: "Main centered cover title.",
  }),
  subtitle: z.string().max(34).default("인력 운영").meta({
    description: "Subtitle beneath the title.",
  }),

  footerItems: z
    .array(FooterMetaSchema)
    .max(2)
    .default([
      { label: "발표자", value: "발표자 이름" },
      { label: "날짜", value: "2026년 12월 4일" },
    ])
    .meta({
      description: "Footer metadata groups.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const CenteredCoverWithFooterMeta = ({
  data,
}: {
  data: Partial<SchemaType>;
}) => {
  const slideData = data as SchemaType;

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap"
        rel="stylesheet"
      />

      <div
        className="relative h-[720px] w-[1280px] flex items-center justify-center overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#27292d)",
          fontFamily: "var(--body-font-family,'DM Serif Display')",
        }}
      >
        <div className=" text-center ">
          <h1
            className="text-[124px] leading-[124.6px]"
            style={{ color: "var(--background-text,#dddac7)" }}
          >
            {slideData.title}
          </h1>
          <p
            className="mt-[22px] text-[33px] tracking-[0.02em]"
            style={{ color: "var(--background-text,#d7d3be)" }}
          >
            {slideData.subtitle}
          </p>
        </div>

        <div
          className="absolute bottom-[34px] left-[36px] flex gap-[74px]"
          style={{ color: "var(--background-text,#d7d3be)" }}
        >
          {slideData.footerItems.map((item, index) => (
            <div key={`${item.label}-${index}`}>
              <p
                className="text-[14px] font-semibold tracking-[0.04em]"
                style={{ color: "var(--background-text,#cbc7b2)" }}
              >
                {item.label}
              </p>
              <p className="mt-[12px] text-[15px] leading-none">{item.value}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default CenteredCoverWithFooterMeta;
