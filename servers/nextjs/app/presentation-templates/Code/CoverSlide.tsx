import * as z from "zod";

export const slideLayoutId = "cover-slide";
export const slideLayoutName = "표지 슬라이드";
export const slideLayoutDescription =
  "조직/기관/발표자, 프레젠테이션 제목/헤딩, 보조 부제목이 포함된 오프닝/표지/소개 슬라이드.";

export const Schema = z.object({
  companyName: z.string().min(2).max(18).optional().default("회사명").meta({
    description: "Optional organization/institution/presenter name shown above the slide title.",
  }),
  title: z.string().min(8).max(28).default("개발 로드맵").meta({
    description: "Title/heading of the slide.",
  }),
  subtitle: z
    .string()
    .min(24)
    .max(40)
    .default(
      "체계적인 개발 프로세스를 통해 아이디어를 시장에 바로 출시할 수 있는 솔루션으로 만들어 갑니다."
    )
    .meta({
      description: "Supporting subtitle shown under the heading.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide01RoadmapCover = ({ data }: { data: Partial<SchemaType> }) => {

  return (<>
    <link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:ital,opsz,wght@0,6..12,200..1000;1,6..12,200..1000&display=swap" rel="stylesheet" />
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden p-[53px]"
      style={{
        backgroundColor: "var(--background-color,#101B37)",
        fontFamily: "var(--body-font-family,Nunito Sans)",
      }}
    >
      <div className="relative z-10 flex h-full flex-col items-center justify-center px-[200px] text-center">
        <p className="text-[22px]" style={{ color: "var(--background-text,#d7dcff)" }}>{data.companyName}</p>
        <h2 className="mt-[10px] text-[64px] font-medium" style={{ color: "var(--background-text,#ffffff)" }}>
          {data.title}
        </h2>
        <p className="mt-[35px] text-[26px] leading-[132%]" style={{ color: "var(--background-text,#d8ddff)" }}>{data.subtitle}</p>
      </div>
    </div>
  </>
  );
};

export default CodeSlide01RoadmapCover;
