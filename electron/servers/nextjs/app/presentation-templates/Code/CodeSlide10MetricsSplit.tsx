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

export const slideLayoutId = "code-metrics-split-slide";
export const slideLayoutName = "Code Metrics Split Slide";
export const slideLayoutDescription =
  "A metrics slide with narrative text on the left and two stat cards on the right.";

export const Schema = z.object({
  title: z.string().min(6).max(18).default("Metrics").meta({
    description: "Slide title shown at the top-left.",
  }),
  explanationTitle: z.string().min(4).max(16).default("Explanation").meta({
    description: "Heading above the explanatory paragraph.",
  }),
  explanation: z
    .string()
    .max(320)
    .default(
      "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
    )
    .meta({
      description: "Body text for the narrative section.",
    }),
  metrics: z
    .array(MetricSchema)
    .min(2)
    .max(2)
    .default([
      { value: "50k+", label: "Active Users", period: "Last 12 months" },
      { value: "50k+", label: "Active Users", period: "Last 12 months" },
    ])
    .meta({
      description: "Two metric cards shown in the right column.",
    }),
  pageLabel: z.string().min(3).max(8).default("10 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide10MetricsSplit = ({ data }: { data: Partial<SchemaType> }) => {


  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden p-[53px]"
      style={{
        backgroundColor: "var(--background-color,#101B37)",
        color: "var(--background-text,#ffffff)",
        fontFamily: "var(--body-font-family,Nunito Sans)",
      }}
    >


      <h2 className="text-[64px] font-medium tracking-[-0.03em]" style={{ color: "var(--background-text,#f2f4ff)" }}>{data.title}</h2>
      <div className="relative z-10 flex gap-10 ">
        <div className="w-1/2">
          <h3 className="mt-[28px] text-[24px] font-medium" style={{ color: "var(--background-text,#f1f4ff)" }}>{data.explanationTitle}</h3>
          <p className="mt-[16px] text-[22px] leading-[145%]" style={{ color: "var(--background-text,#d2d9ff)" }}>{data.explanation}</p>
        </div>

        <div className="flex flex-col justify-center items-end gap-[25px] w-1/2">
          {data?.metrics?.map((metric, index) => (
            <div
              key={`metric-grid-${index}`}
              className="rounded-[16px] w-[310px] border pt-[26px] px-[26px] pb-[16px] text-center"
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
      </div>

      <div
        className="absolute bottom-[26px] left-1/2 -translate-x-1/2 rounded-full border px-[22px] py-[8px] text-[14px]"
        style={{
          borderColor: "var(--stroke,#31415880)",
          backgroundColor: "var(--card-color,#1D293DCC)",
          color: "var(--background-text,#CAD5E2)",
        }}
      >
        {data?.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide10MetricsSplit;
