import * as z from "zod";


import { WorkflowBarChart } from "./chartPrimitives";

const InsightItemSchema = z.object({
  title: z.string().min(3).max(18).meta({
    description: "Short insight title shown next to the icon.",
  }),
  description: z.string().min(20).max(84).meta({
    description: "Supporting text shown below the insight title.",
  }),
});

const ChartPointSchema = z.object({
  label: z.string().min(1).max(12).meta({
    description: "Chart axis label.",
  }),
  value: z.number().min(0).max(1000).meta({
    description: "Bar chart value.",
  }),
});

export const slideLayoutId = "data-analysis-bar-slide";
export const slideLayoutName = "Data Analysis Bar Slide";
export const slideLayoutDescription =
  "A slide with a title at the top, a vertical list of three analysis points on the left, and a bar chart on the right. Each analysis point contains a small icon badge, a short title, and a supporting description.";

export const Schema = z.object({
  title: z.string().min(3).max(28).default("Data Analysis").meta({
    description: "Slide title shown at the top-left.",
  }),
  itemIcon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().default("pulse icon"),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "pulse icon",
  }).meta({
    description: "Icon shown in each analysis item badge.",
  }),
  items: z
    .array(InsightItemSchema)
    .min(3)
    .max(3)
    .default([
      { title: "Title 1", description: "Ut enim ad minima veniam, quis." },
      { title: "Title 2", description: "Ut enim ad minima veniam, quis." },
      { title: "Title 2", description: "Ut enim ad minima veniam, quis." },
    ])
    .meta({
      description: "Three analysis points shown in the left column.",
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
      description: "Weekly values shown in the bar chart.",
    }),
  legendLabel: z.string().min(3).max(32).default("Traditional Workflow").meta({
    description: "Legend label shown below the chart.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const DataAnalysisBarSlide = ({ data }: { data: Partial<SchemaType> }) => {

  const { title, itemIcon, items, chartData, legendLabel } = data;

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

      <div className="flex justify-between px-[85px] pt-[44px]">
        <div className="space-y-[38px] pt-[8px]">
          {items?.map((item, index) => (
            <div key={`${item.title}-${index}`}>
              <div className="flex items-center gap-[14px]">
                <div className="flex h-[55px] w-[55px] items-center justify-center rounded-full bg-[#4d4ef3] text-white">
                  <img
                    src={itemIcon?.__icon_url__}
                    alt={itemIcon?.__icon_query__}
                    className="h-[25px] w-[25px] object-contain"
                    style={{ filter: "brightness(0) invert(1)" }}
                  />
                </div>
                <h3 className="text-[20px] font-medium tracking-[2.074px] text-[#232223]">
                  {item.title}
                </h3>
              </div>
              <p className="mt-[20px]  text-[24px] leading-[26.667px] text-[#232223]">
                {item.description}
              </p>
            </div>
          ))}
        </div>

        <div className="ml-[44px] flex flex-col items-center">
          <div className="h-[346px] w-[560px]">
            <WorkflowBarChart data={chartData ?? []} />
          </div>
          <div className="mt-[12px] flex items-center gap-[10px] text-[24px] tracking-[-0.03em] text-[#4d4ef3]">
            <span className="h-[12px] w-[12px] rounded-full bg-[#4d4ef3]" />
            <p>{legendLabel}</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DataAnalysisBarSlide;
