import * as z from "zod";


import { WorkflowBarChart } from "./chartPrimitives";

const ChartPointSchema = z.object({
  label: z.string().min(1).max(12).meta({
    description: "Chart axis label.",
  }),
  value: z.number().min(0).max(1000).meta({
    description: "Bar chart value.",
  }),
});

export const slideLayoutId = "data-analysis-insight-bar-slide";
export const slideLayoutName = "Data Analysis Insight Bar Slide";
export const slideLayoutDescription =
  "A slide with a title at the top, a single featured insight block on the left containing an icon badge and a paragraph, and a bar chart on the right with a legend below it.";

export const Schema = z.object({
  title: z.string().min(3).max(28).default("Data Analysis").meta({
    description: "Slide title shown at the top-left.",
  }),
  insightIcon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().default("pulse icon"),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "pulse icon",
  }).meta({
    description: "Icon shown in the featured insight badge.",
  }),
  insightBody: z.string().min(80).max(320).default(
    "Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut alut enim ad minima veniam, quis. Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut alut enim ad minima veniam, quis"
  ).meta({
    description: "Featured insight paragraph shown in the left content area.",
  }),
  chartData: z
    .array(ChartPointSchema)
    .min(7)
    .max(7)
    .default([
      { label: "Mon", value: 120 },
      { label: "Tue", value: 200 },
      { label: "Wed", value: 150 },
      { label: "Thu", value: 80 },
      { label: "Fri", value: 70 },
      { label: "Sat", value: 110 },
      { label: "Sun", value: 130 },
    ])
    .meta({
      description: "Weekly values shown in the right-side bar chart.",
    }),
  legendLabel: z.string().min(3).max(32).default("Traditional Workflow").meta({
    description: "Legend label shown below the chart.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const DataAnalysisInsightBarSlide = ({
  data,
}: {
  data: Partial<SchemaType>;
}) => {

  const { title, insightIcon, insightBody, chartData, legendLabel } = data;

  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#f9f8f8]">
      <div
        className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
        style={{ height: 185 }}
      />

      <div className="px-[64px] pt-[48px]">
        <h2 className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]">
          {title}
        </h2>
      </div>

      <div className="flex justify-between px-[74px] pt-[96px]">
        <div className="w-[380px] pt-[24px]">
          <div className="flex items-center gap-[14px]">
            <div className="flex h-[55px] w-[55px] items-center justify-center rounded-full bg-[#157CFF] text-white">
              <img
                src={insightIcon?.__icon_url__}
                alt={insightIcon?.__icon_query__}
                className="h-[25px] w-[25px] object-contain"

              />
            </div>
          </div>
          <p className="mt-[20px] text-[24px] leading-[26.667px] text-[#232223]">
            {insightBody}
          </p>
        </div>

        <div className="ml-[28px] flex flex-col items-center">
          <div className="h-[346px] w-[560px]">
            <WorkflowBarChart data={chartData ?? []} />
          </div>
          <div className="mt-[12px] flex items-center gap-[10px] text-[24px] tracking-[-0.03em] text-[#157CFF]">
            <span className="h-[12px] w-[12px] rounded-full bg-[#157CFF]" />
            <p>{legendLabel}</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DataAnalysisInsightBarSlide;
