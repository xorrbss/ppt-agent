"use client";

import type { ReactNode } from "react";
import * as z from "zod";

import {
  DivergingDataPointSchema,
  FlexibleReportChart,
  MultiSeriesDataPointSchema,
  ScatterDataPointSchema,
  SimpleDataPointSchema,
  flexibleChartDataSchema,
  flexibleChartTypeSchema,
  type FlexibleChartData,
} from "./flexibleReportChart";
import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";

const SummaryCardSchema = z.object({
  value: z.string().min(1).max(8).meta({
    description: "Primary metric value shown in the compact summary card.",
  }),
  label: z.string().min(3).max(20).meta({
    description: "Short summary card label.",
  }),
  icon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().default("pulse icon"),
  }).optional().meta({
    description: "Icon shown in each compact summary card.",
  }).default({
    __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "pulse icon",
  }),
});








export const slideLayoutId = "data-analysis-dashboard-slide";
export const slideLayoutName = "데이터 분석 대시보드 슬라이드";
export const slideLayoutDescription =
  "제목, 요약 카드, 그리고 반응형 차트 패널 그리드(1~9개)로 구성된 대시보드 형식의 슬라이드. 각 패널은 다른 보고서 슬라이드와 동일한 유연한 차트 유형을 사용하며, 작은 셀에 맞게 라벨과 여백이 압축되어 있습니다.";

const ChartItemSchema = z.object({

  type: flexibleChartTypeSchema.default('bar'),
  data: z.union([
    z.array(SimpleDataPointSchema),
    z.array(MultiSeriesDataPointSchema),
    z.array(DivergingDataPointSchema),
    z.array(ScatterDataPointSchema),
  ]).default([
    { name: 'Q1', value: 45 },
    { name: 'Q2', value: 72 },
    { name: 'Q3', value: 58 },
    { name: 'Q4', value: 89 },
  ]),
  series: z.array(z.string()).optional(),
});

export const Schema = z.object({
  title: z.string().min(3).max(12).default("데이터 분석").meta({
    description: "Slide title shown at the top-left.",
  }),

  summaryCards: z
    .array(SummaryCardSchema)
    .min(2)
    .max(4)
    .optional()
    .default([
      {
        value: "5", label: "텍스트 1", icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "placeholder icon",
        }
      },
      {
        value: "52", label: "텍스트 2", icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "placeholder icon",
        }
      },
      {
        value: "4", label: "텍스트 3", icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "placeholder icon",
        }
      },
      {
        value: "80%", label: "텍스트 4", icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "placeholder icon",
        }
      },
    ])
    .meta({
      description: "Four compact summary cards displayed above the dashboard panels.",
    }),
  charts: z.array(ChartItemSchema).min(1).max(6).default([
    { type: 'bar', data: [{ name: '1분기', value: 125000 }, { name: '2분기', value: 158000 }, { name: '3분기', value: 142000 }, { name: '4분기', value: 189000 }] },
    { type: 'donut', data: [{ name: '북미', value: 35 }, { name: '유럽', value: 28 }, { name: '아시아 태평양', value: 25 }, { name: '기타', value: 12 }] },
    { type: 'line', data: [{ name: '1월', value: 30 }, { name: '2월', value: 45 }, { name: '3월', value: 52 }, { name: '4월', value: 48 }, { name: '5월', value: 67 }, { name: '6월', value: 82 }] },
    { type: 'bar', data: [{ name: '영업', value: 87 }, { name: '마케팅', value: 72 }, { name: '엔지니어링', value: 95 }, { name: '지원', value: 68 }] },
    { type: 'bar-clustered', data: [{ name: '1분기', values: { 'Product A': 45, 'Product B': 62 } }, { name: '2분기', values: { 'Product A': 58, 'Product B': 71 } }, { name: '3분기', values: { 'Product A': 72, 'Product B': 65 } }], series: ['Product A', 'Product B'] },
    { type: 'bar-diverging', data: [{ name: '품질', positive: 78, negative: 22 }, { name: '서비스', positive: 65, negative: 35 }, { name: '가격', positive: 42, negative: 58 }], series: ['만족', '불만족'] },
  ]),
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
    <div className="flex gap-[10px]  items-center rounded-[14px]  py-[9px]">
      <div
        className="flex h-[36px] w-[36px] items-center justify-center border border-[#ECF5FE] shrink-0 rounded-full bg-[#ECF5FE] "
        style={{
          backgroundColor: "var(--primary-color,#ECF5FE)",
          borderColor: "var(--stroke,#ECF5FE)",
        }}
      >
        <RemoteSvgIcon
          url={iconUrl ?? ""}
          strokeColor={"currentColor"}
          className="h-[18px] w-[18px] object-contain"
          color="var(--primary-text, #000000)"
          title={iconAlt ?? ""}
        />

      </div>
      <div className="">
        <p
          className="text-[18px] leading-none tracking-[-0.04em] text-[#4A4D53]"
          style={{ color: "var(--background-text,#4A4D53)" }}
        >
          {value}
        </p>
        <p
          className="mt-[4px] text-[14px] leading-none text-[#6C6C6C]"
          style={{ color: "var(--background-text,#6C6C6C)" }}
        >
          {label}
        </p>
      </div>
    </div>
  );
}





const DataAnalysisDashboardSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, summaryCards, charts } = data;
  const halfChart = charts?.slice(0, Math.ceil(charts.length / 2));
  const otherHalfChart = charts?.slice(Math.ceil(charts.length / 2));

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,200..900;1,200..900&display=swap" rel="stylesheet" />
      <div
        className="relative flex flex-col  h-[720px] w-[1280px] overflow-hidden  bg-[#F9F8F8]"
        style={{
          backgroundColor: "var(--background-color,#F9F8F8)",
          fontFamily: "var(--body-font-family,'Source Sans 3')",
        }}
      >
        <div
          className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
          style={{ height: 185, backgroundColor: "var(--primary-color,#157CFF)" }}
        />

        <div className="px-[64px] pt-[48px]">
          <h2
            className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {title}
          </h2>
        </div>

        {summaryCards && summaryCards.length > 0 && <div className=" mx-[64px] grid bg-white gap-[16px] p-[13px] mt-[22px] rounded-[14px]  "
          style={{
            gridTemplateColumns: `repeat(${summaryCards.length}, minmax(220px, 1fr))`,
            backgroundColor: "var(--card-color,#ffffff)",
          }}>
          {summaryCards?.map((card, index) => (
            <SummaryCard
              key={`${card.label}-${index}`}
              value={card.value}
              label={card.label}
              iconUrl={card.icon?.__icon_url__}
              iconAlt={card.icon?.__icon_query__}
            />
          ))}
        </div>}
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden pb-[30px]">

          {halfChart && halfChart.length > 0 && <div className="mt-[14px] min-h-0 px-[64px] flex-1 overflow-hidden"
            style={{
              height: otherHalfChart && otherHalfChart?.length > 0 ? '200px' : 'auto',
            }}
          >
            <div
              className={`grid h-full min-h-0 overflow-hidden bg-white p-[13px] rounded-[14px] gap-[10px] `}
              style={{
                gridTemplateColumns: `repeat(${halfChart.length}, minmax(150px, 1fr))`,
                backgroundColor: "var(--card-color,#ffffff)",
              }}
            >
              {halfChart?.map((chart, index) => (
                <div
                  key={index}
                  className="rounded-[6px] min-h-0 flex flex-col overflow-hidden"

                >

                  <div className="flex-1 min-h-0 overflow-hidden" >
                    <FlexibleReportChart density="compact" chartType={chart.type} data={chart.data} series={chart.series} />
                  </div>
                </div>
              ))}
            </div>
          </div>}
          {otherHalfChart && otherHalfChart.length > 0 && <div className="mt-[14px] min-h-0 px-[64px] flex-1 h-[200px] overflow-hidden">
            <div
              className={`grid h-full min-h-0 overflow-hidden bg-white p-[13px] rounded-[14px] gap-[10px] `}
              style={{
                gridTemplateColumns: `repeat(${otherHalfChart.length}, minmax(150px, 1fr))`,
                backgroundColor: "var(--card-color,#ffffff)",
              }}
            >
              {otherHalfChart?.map((chart, index) => (
                <div
                  key={index}
                  className="rounded-[6px] min-h-0 flex flex-col overflow-hidden"
                >
                  <div className="flex-1 min-h-0 overflow-hidden" >
                    <FlexibleReportChart density="compact" chartType={chart.type} data={chart.data} series={chart.series} />
                  </div>
                </div>
              ))}
            </div>
          </div>}
        </div>
      </div>
    </>
  );
};

export default DataAnalysisDashboardSlide;
