import * as z from "zod";

const MetricSchema = z.object({
  value: z.string().min(2).max(6).meta({
    description: "Primary metric value.",
  }),
  label: z.string().min(3).max(14).meta({
    description: "Metric label text.",
  }),
  period: z.string().min(3).max(16).meta({
    description: "Metric period text.",
  }),
});

export const slideLayoutId = "code-metrics-grid-slide";
export const slideLayoutName = "Code Metrics Grid Slide";
export const slideLayoutDescription =
  "A six-card metrics grid for KPI overviews in code-focused decks.";

export const Schema = z.object({
  title: z.string().min(6).max(18).default("Metrics").meta({
    description: "Slide heading shown above the KPI cards.",
  }),
  metrics: z
    .array(MetricSchema)
    .min(3)
    .max(6)
    .default([
      { value: "99.9%", label: "Uptime", period: "Last 12 months" },
      { value: "<100ms", label: "Response Time", period: "Last 12 months" },
      { value: "50k+", label: "Active Users", period: "Last 12 months" },
      { value: "99.9%", label: "Uptime", period: "Last 12 months" },
      { value: "<100ms", label: "Response Time", period: "Last 12 months" },
      { value: "50k+", label: "Active Users", period: "Last 12 months" },
    ])
    .meta({
      description: "Six KPI cards in a 3x2 grid.",
    }),
  pageLabel: z.string().min(3).max(8).default("11 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide11MetricsGrid = ({ data }: { data: Partial<SchemaType> }) => {

  return (
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
            <p className="mt-[13px] text-[18px]" style={{ color: "var(--background-text,#8fa2d8)" }}>{metric.period}</p>
          </div>
        ))}
      </div>


      <div
        className="absolute bottom-[26px] left-1/2 -translate-x-1/2 rounded-full border px-[22px] py-[8px] text-[14px]"
        style={{
          borderColor: "var(--stroke,#31415880)",
          backgroundColor: "var(--card-color,#1D293DCC)",
          color: "var(--background-text,#CAD5E2)",
        }}
      >
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide11MetricsGrid;
