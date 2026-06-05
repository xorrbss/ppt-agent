import * as z from "zod";

export const slideLayoutId = "full-width-statement";
export const slideLayoutName = "전체 너비 문구";
export const slideLayoutDescription =
  "간결한 라벨과 큰 전체 너비 문구 블록으로 구성된 미니멀한 강조 레이아웃입니다.";

export const Schema = z.object({
  label: z.string().max(12).default("라벨").meta({
    description: "Small label above the statement.",
  }),
  statement: z
    .string()

    .max(90)
    .default(
      "이것은 프레젠테이션에서 자리표시자 내용으로 사용되는 예시 문구입니다. 실제 발표 내용으로 자유롭게 교체하세요."
    )
    .meta({
      description: "Main statement text, with max 90 characters.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const FullWidthStatement = ({ data }: { data: Partial<SchemaType> }) => {
  const slideData = data as SchemaType;

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap"
        rel="stylesheet"
      />

      <div
        className="relative h-[720px] w-[1280px] flex flex-col justify-end pb-[74px] overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#27292d)",
          fontFamily: "var(--body-font-family,'DM Serif Display')",
        }}
      >
        <div className="px-[46px] ">
          <p
            className="text-[32px] leading-none"
            style={{ color: "var(--background-text,#d7d3be)" }}
          >
            {slideData.label}
          </p>

          <p
            className="mt-[61px]  text-[100px] leading-[100%]"
            style={{
              color: "var(--background-text,#dddac7)",
            }}
          >
            {slideData.statement}”
          </p>
        </div>
      </div>
    </>
  );
};

export default FullWidthStatement;
