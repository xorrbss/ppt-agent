import * as z from "zod";

const MetricSchema = z.object({
  value: z.string().min(2).max(6).meta({
    description: "Primary metric value.",
  }),
  label: z.string().min(3).max(15).meta({
    description: "Metric label text.",
  }),
  subtext: z.string().min(3).max(30).meta({
      description: "Metric subtext/description.",
  }),
});

export const slideLayoutId = "metrics-grid-slide";
export const slideLayoutName = "지표 그리드 슬라이드";
export const slideLayoutDescription =
  "상단에 제목과 지표 카드 그리드가 있는 슬라이드.";

export const Schema = z.object({
  title: z.string().min(6).max(18).default("지표").meta({
    description: "Slide heading shown above the KPI cards.",
  }),
  metrics: z
    .array(MetricSchema)
    .min(1)
    .max(6)
    .default([
      { value: "99.9%", label: "가동 시간", subtext: "최근 12개월" },
      { value: "<100ms", label: "응답 시간", subtext: "최근 12개월" },
      { value: "50k+", label: "활성 사용자", subtext: "최근 12개월" },
      { value: "99.9%", label: "가동 시간", subtext: "최근 12개월" },
      { value: "<100ms", label: "응답 시간", subtext: "최근 12개월" },
      { value: "50k+", label: "활성 사용자", subtext: "최근 12개월" },
    ])
    .meta({
      description: "Metrics cards in a grid.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide11MetricsGrid = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:ital,opsz,wght@0,6..12,200..1000;1,6..12,200..1000&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] border p-[40px]"
        style={{
          borderColor: "var(--stroke,#243272)",
          backgroundColor: "var(--background-color,#101B37)",
          color: "var(--background-text,#edf1ff)",
          fontFamily: "var(--body-font-family,Nunito Sans)",
        }}
      >



        <h2 className="text-[64px] font-medium" style={{ color: "var(--background-text,#ffffff)" }}>{data.title}</h2>

        <div className="mt-[53px] grid flex-1 grid-cols-3 gap-[14px]">
          {data?.metrics?.map((metric, index) => (
            <div
              key={`metric-grid-${index}`}
              className="rounded-[16px] border pt-[26px] px-[26px] pb-[16px] text-center"
              style={{
                borderColor: "var(--stroke,#1D293D80)",
                backgroundColor: "var(--card-color,#0F172B80)",
              }}
            >
              <p className="text-[64px] font-semibold leading-none" style={{ color: "var(--graph-0,#8bb4ff)" }}>{metric.value}</p>
              <p className="mt-[13px] text-[26px]" style={{ color: "var(--background-text,#edf1ff)" }}>{metric.label}</p>
              <p className="mt-[13px] text-[18px]" style={{ color: "var(--background-text,#8fa2d8)" }}>{metric.subtext}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default CodeSlide11MetricsGrid;
