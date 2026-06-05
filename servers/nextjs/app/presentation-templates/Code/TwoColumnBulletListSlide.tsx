import * as z from "zod";

export const slideLayoutId = "bullet-list-slide";
export const slideLayoutName = "2열 글머리 목록 슬라이드";
export const slideLayoutDescription =
  "항목이 있는 2열 번호 목록.";

export const Schema = z.object({
  title: z.string().min(6).max(30).default("활용 사례").meta({
    description: "Slide title shown above the numbered list.",
  }),
  items: z
    .array(z.string().min(1).max(200))
    .min(1)
    .max(8)
    .default([
      "UI 일관성을 위해 사전 구축된 컴포넌트 라이브러리 사용",
      "타입 안정성을 위해 TypeScript로 REST API 통합",
      "WebSocket을 사용해 실시간 업데이트 구현",
      "자동화된 CI/CD 파이프라인으로 프로덕션 배포",
      "보호된 작업을 위한 역할 기반 권한 적용",
      "라우트 계약에서 문서 자동 생성",
      "텔레메트리 대시보드로 릴리스 상태 추적",
      "고위험 배포를 위한 롤백 전략 추가",
    ])
    .meta({
      description: "Eight use-case items shown in two columns.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide07UseCaseList = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:ital,opsz,wght@0,6..12,200..1000;1,6..12,200..1000&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden p-[53px]"
        style={{
          backgroundColor: "var(--background-color,#101B37)",
          fontFamily: "var(--body-font-family,Nunito Sans)",
        }}
      >


        <h2 className="text-[64px] font-medium" style={{ color: "var(--background-text,#f2f4ff)" }}>{data.title}</h2>

        <div className="mt-[53px] grid flex-1 grid-cols-2 gap-[21px]">
          {data?.items?.map((item, index) => (
            <div
              key={`use-case-${index}`}
              className="flex items-center gap-[21px] rounded-[18px] border p-[28px]"
              style={{
                boxShadow: "0 33.333px 66.667px -16px rgba(0, 0, 0, 0.25)",
                borderColor: "var(--stroke,#1D293D80)",
                backgroundColor: "var(--card-color,#0F172B80)",
              }}
            >
              <span
                className="flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-full border text-[18px]"
                style={{
                  borderColor: "var(--primary-color,#2B7FFF4D)",
                  backgroundColor: "var(--primary-color,#2B7FFF33)",
                  color: "var(--primary-text,#51A2FF)",
                }}
              >
                {index + 1}
              </span>
              <p className="text-[18px]" style={{ color: "var(--background-text,#d5dcff)" }}>{item}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default CodeSlide07UseCaseList;
