import type { ReactNode } from "react";
import * as z from "zod";

import {
  AreaTrendChart,
  CompactBarChart,
  CompactPieChart,
  SemiDonutChart,
  TrendLineChart,
} from "./chartPrimitives";

const SummaryCardSchema = z.object({
  value: z.string().min(1).max(8).meta({
    description: "Primary metric value shown in the compact summary card.",
  }),
  label: z.string().min(3).max(20).meta({
    description: "Short summary card label.",
  }),
});

const ChartPointSchema = z.object({
  label: z.string().min(1).max(12).meta({
    description: "Chart axis label.",
  }),
  value: z.number().min(0).max(1000).meta({
    description: "Single-series chart value.",
  }),
});

const DualChartPointSchema = z.object({
  label: z.string().min(1).max(12).meta({
    description: "Chart axis label.",
  }),
  valueA: z.number().min(0).max(1000).meta({
    description: "First series value.",
  }),
  valueB: z.number().min(0).max(1000).meta({
    description: "Second series value.",
  }),
});

const PieSegmentSchema = z.object({
  name: z.string().min(1).max(18).meta({
    description: "Category name shown in chart legends.",
  }),
  value: z.number().min(1).max(1000).meta({
    description: "Category value used in the chart.",
  }),
});

export const slideLayoutId = "data-analysis-dashboard-slide";
export const slideLayoutName = "Data Analysis Dashboard Slide";
export const slideLayoutDescription =
  "A dashboard-style slide with a title at the top, a row of compact summary cards underneath, and two stacked dashboard panels below. Each panel is split into three chart cells, creating a six-chart overview made of bar, donut, line, area, pie, and comparison charts.";

export const Schema = z.object({
  title: z.string().min(3).max(28).default("Data Analysis").meta({
    description: "Slide title shown at the top-left.",
  }),
  summaryIcon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().default("pulse icon"),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "pulse icon",
  }).meta({
    description: "Icon shown in each compact summary card.",
  }),
  summaryCards: z
    .array(SummaryCardSchema)
    .min(4)
    .max(4)
    .default([
      { value: "5", label: "Text 1" },
      { value: "52", label: "Text 2" },
      { value: "4", label: "Text 3" },
      { value: "80%", label: "Text 4" },
    ])
    .meta({
      description: "Four compact summary cards displayed above the dashboard panels.",
    }),
  workflowBars: z
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
      description: "Bar chart data shown in the top-left dashboard cell.",
    }),
  gaugeSegments: z
    .array(PieSegmentSchema)
    .min(3)
    .max(3)
    .default([
      { name: "Category A", value: 45 },
      { name: "Category B", value: 30 },
      { name: "Category C", value: 25 },
    ])
    .meta({
      description: "Three segments used in the top-center semi-donut chart.",
    }),
  trendSeries: z
    .array(DualChartPointSchema)
    .min(7)
    .max(7)
    .default([
      { label: "Label", valueA: 22, valueB: 35 },
      { label: "Label", valueA: 54, valueB: 26 },
      { label: "Label", valueA: 44, valueB: 70 },
      { label: "Label", valueA: 78, valueB: 52 },
      { label: "Label", valueA: 50, valueB: 44 },
      { label: "Label", valueA: 32, valueB: 60 },
      { label: "Label", valueA: 58, valueB: 40 },
    ])
    .meta({
      description: "Two-series line chart data shown in the top-right cell.",
    }),
  detailedArea: z
    .array(ChartPointSchema)
    .min(7)
    .max(7)
    .default([
      { label: "12:00", value: 22 },
      { label: "13:00", value: 64 },
      { label: "14:00", value: 48 },
      { label: "15:00", value: 56 },
      { label: "16:00", value: 41 },
      { label: "17:00", value: 58 },
      { label: "18:00", value: 63 },
    ])
    .meta({
      description: "Area chart data shown in the bottom-left dashboard cell.",
    }),
  shareBreakdown: z
    .array(PieSegmentSchema)
    .min(3)
    .max(3)
    .default([
      { name: "Category A", value: 50 },
      { name: "Category B", value: 30 },
      { name: "Category C", value: 20 },
    ])
    .meta({
      description: "Pie chart data shown in the bottom-center dashboard cell.",
    }),
  comparisonBars: z
    .array(ChartPointSchema)
    .min(7)
    .max(7)
    .default([
      { label: "Jan", value: 70 },
      { label: "Feb", value: 170 },
      { label: "Mar", value: 110 },
      { label: "Apr", value: 42 },
      { label: "May", value: 88 },
      { label: "Jun", value: 106 },
      { label: "Jul", value: 112 },
    ])
    .meta({
      description: "Bar chart data shown in the bottom-right dashboard cell.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

function SummaryCard({
  value,
  label,
  iconUrl,
  iconAlt,
}: {
  value: string;
  label: string;
  iconUrl?: string;
  iconAlt?: string;
}) {
  return (
    <div className="flex h-[74px] items-center rounded-[14px] bg-white px-[16px]">
      <div className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-[#4d4ef3] text-white">
        <img
          src={iconUrl ?? ""}
          alt={iconAlt ?? ""}
          className="h-[10px] w-[10px] object-contain"
          style={{ filter: "brightness(0) invert(1)" }}
        />
      </div>
      <div className="ml-[10px]">
        <p className="text-[22px] leading-none tracking-[-0.04em] text-[#232223]">
          {value}
        </p>
        <p className="mt-[4px] text-[12px] leading-none text-[#535665]">{label}</p>
      </div>
    </div>
  );
}

function ChartCell({
  children,
  footer,
  topLegend,
}: {
  children: ReactNode;
  footer?: ReactNode;
  topLegend?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col px-[10px] py-[10px]">
      {topLegend && <div className="mb-[4px] flex justify-center">{topLegend}</div>}
      <div className="min-h-0 flex-1">{children}</div>
      {footer && <div className="mt-[4px] flex justify-center">{footer}</div>}
    </div>
  );
}

function DotLegend({
  items,
}: {
  items: { label: string; color: string }[];
}) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-[10px] text-[8px] text-[#6b7280]">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-[4px]">
          <span
            className="block h-[6px] w-[6px] rounded-full"
            style={{ backgroundColor: item.color }}
          />
          {item.label}
        </span>
      ))}
    </div>
  );
}

const DataAnalysisDashboardSlide = ({ data }: { data: Partial<SchemaType> }) => {

  const { title, summaryIcon, summaryCards, workflowBars, gaugeSegments, trendSeries, detailedArea, shareBreakdown, comparisonBars } = data;

  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#f9f8f8]">
      <div
        className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#4d4ef3]"
        style={{ height: 188 }}
      />

      <div className="px-[74px] pt-[44px]">
        <h2 className="text-[78px] font-semibold leading-none tracking-[-0.06em] text-[#232223]">
          {title}
        </h2>
      </div>

      <div className="grid grid-cols-4 gap-[16px] px-[74px] pt-[14px]">
        {summaryCards?.map((card, index) => (
          <SummaryCard
            key={`${card.label}-${index}`}
            value={card.value}
            label={card.label}
            iconUrl={summaryIcon?.__icon_url__}
            iconAlt={summaryIcon?.__icon_query__}
          />
        ))}
      </div>

      <div className="flex flex-col gap-[12px] px-[74px] pt-[12px]">
        <div className="grid h-[168px] grid-cols-3 divide-x divide-[#ecf0f6] rounded-[16px] bg-white">
          <ChartCell
            footer={
              <DotLegend items={[{ label: "Traditional Workflow", color: "#4d4ef3" }]} />
            }
          >
            <CompactBarChart data={workflowBars ?? []} />
          </ChartCell>

          <ChartCell
            footer={
              <DotLegend
                items={[
                  { label: "Category A", color: "#4d4ef3" },
                  { label: "Category B", color: "#9fb6ff" },
                  { label: "Category C", color: "#e8eefb" },
                ]}
              />
            }
          >
            <SemiDonutChart data={gaugeSegments ?? []} />
          </ChartCell>

          <ChartCell
            topLegend={
              <div className="flex gap-[10px] text-[8px] text-[#6b7280]">
                <p>Category A</p>
                <p>Category B</p>
              </div>
            }
          >
            <TrendLineChart data={trendSeries ?? []} />
          </ChartCell>
        </div>

        <div className="grid h-[168px] grid-cols-3 divide-x divide-[#ecf0f6] rounded-[16px] bg-white">
          <ChartCell
            footer={
              <DotLegend items={[{ label: "Detailed Workflow", color: "#4d4ef3" }]} />
            }
          >
            <AreaTrendChart data={detailedArea ?? []} idPrefix="dashboard-area" />
          </ChartCell>

          <ChartCell
            footer={
              <DotLegend
                items={[
                  { label: "Category A", color: "#4d4ef3" },
                  { label: "Category B", color: "#9fb6ff" },
                  { label: "Category C", color: "#d7dff4" },
                ]}
              />
            }
          >
            <CompactPieChart data={shareBreakdown ?? []} />
          </ChartCell>

          <ChartCell
            footer={
              <DotLegend
                items={[
                  { label: "Category A", color: "#4d4ef3" },
                  { label: "Category B", color: "#9fb6ff" },
                ]}
              />
            }
          >
            <CompactBarChart data={comparisonBars ?? []} />
          </ChartCell>
        </div>
      </div>
    </div>
  );
};

export default DataAnalysisDashboardSlide;
