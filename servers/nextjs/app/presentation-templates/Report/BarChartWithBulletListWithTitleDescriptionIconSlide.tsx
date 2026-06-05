"use client";

import * as z from "zod";

import { FlexibleReportChart, flexibleChartDataSchema } from "./flexibleReportChart";
import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";

const InsightItemSchema = z.object({
  title: z.string().min(3).max(80).meta({
    description: "Bullet point title shown next to the icon.",
  }),
  description: z.string().min(20).max(120).meta({
    description: "Bullet point description shown below the title.",
  }),
});

export const slideLayoutId = "bar-chart-with-bullet-list-title-description-icon-slide";
export const slideLayoutName = "제목·설명·아이콘 글머리 목록과 막대 차트 슬라이드";
export const slideLayoutDescription =
  "상단에 제목이 있고, 왼쪽에는 아이콘·제목·설명이 있는 세 개의 글머리 항목 세로 목록, 오른쪽에는 막대 차트가 배치된 슬라이드.";

export const Schema = z.object({
  title: z.string().min(3).max(80).default("데이터 분석").meta({
    description: "Slide title shown at the top-left.",
  }),
  itemIcon: z
    .object({
      __icon_url__: z
        .string()
        .default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
      __icon_query__: z.string().default("pulse icon"),
    })
    .default({
      __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
      __icon_query__: "pulse icon",
    })
    .meta({
      description: "Icon shown in each analysis item badge.",
    }),
  items: z
    .array(InsightItemSchema)
    .min(1)
    .max(3)
    .default([
      { title: "제목 1", description: "여기에 핵심 분석 내용을 한 줄로 작성하세요." },
      { title: "제목 2", description: "여기에 핵심 분석 내용을 한 줄로 작성하세요." },
      { title: "제목 3", description: "여기에 핵심 분석 내용을 한 줄로 작성하세요." },
    ])
    .meta({
      description: "Three analysis points shown in the left column,maximum 3 items",
    }),
  chartData: flexibleChartDataSchema.default({
    type: "bar",
    data: [
      { name: "월", value: 120 },
      { name: "화", value: 200 },
      { name: "수", value: 150 },
      { name: "목", value: 80 },
      { name: "금", value: 70 },
      { name: "토", value: 110 },
      { name: "일", value: 130 },
    ],

  }),
  legendLabel: z.string().min(3).max(50).default("기존 워크플로").meta({
    description: "Legend label shown below the chart.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const DataAnalysisBarSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, itemIcon, items, chartData, legendLabel } = data;
  const rows = chartData?.data ?? [];
  const chartType = chartData?.type ?? "bar";
  const series = chartData?.series ?? [];

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,200..900;1,200..900&display=swap" rel="stylesheet" />

      <div
        className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#f9f8f8]"
        style={{
          backgroundColor: "var(--background-color,#f9f8f8)",
          fontFamily: "var(--body-font-family,'Source Sans 3')",
        }}
      >
        <div
          className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#4d4ef3]"
          style={{ height: 185, backgroundColor: "var(--graph-0,#4d4ef3)" }}
        />

        <div className="px-[64px] pt-[48px]">
          <h2
            className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {title}
          </h2>
        </div>

        <div className="flex justify-between px-[85px] pt-[44px]">
          <div className="space-y-[38px] pt-[8px]">
            {items?.map((item, index) => (
              <div key={`${item.title}-${index}`}>
                <div className="flex items-center gap-[14px]">
                  <div
                    className="flex h-[55px] w-[55px] items-center justify-center rounded-full bg-[#4d4ef3] text-white"
                    style={{
                      backgroundColor: "var(--graph-0,#4d4ef3)",
                      color: "var(--primary-text,#ffffff)",
                    }}
                  >
                    <RemoteSvgIcon
                      url={itemIcon?.__icon_url__}
                      strokeColor={"currentColor"}
                      className="h-[25px] w-[25px] object-contain"
                      color="var(--primary-text, #ffffff)"
                      title={itemIcon?.__icon_query__}
                    />
                    {/* <img
                      src={itemIcon?.__icon_url__}
                      alt={itemIcon?.__icon_query__}
                      className="h-[25px] w-[25px] object-contain"
                      style={{ filter: "brightness(0) invert(1)" }}
                    /> */}
                  </div>
                  <h3
                    className="text-[20px] font-medium tracking-[2.074px] text-[#232223]"
                    style={{ color: "var(--background-text,#232223)" }}
                  >
                    {item.title}
                  </h3>
                </div>
                <p
                  className="mt-[20px] text-[24px] leading-[26.667px] text-[#232223]"
                  style={{ color: "var(--background-text,#232223)" }}
                >
                  {item.description}
                </p>
              </div>
            ))}
          </div>

          <div className="ml-[44px] flex flex-col items-center">
            <div className="h-[346px] min-h-0 w-[560px] overflow-hidden">
              <FlexibleReportChart chartType={chartType} data={rows} series={series} colorFallback="#4d4ef3" />
            </div>
            <div
              className="mt-[12px] flex items-center gap-[10px] text-[24px] tracking-[-0.03em] text-[#4d4ef3]"
              style={{ color: "var(--graph-0,#4d4ef3)" }}
            >
              <span
                className="h-[12px] w-[12px] rounded-full bg-[#4d4ef3]"
                style={{ backgroundColor: "var(--graph-0,#4d4ef3)" }}
              />
              <p>{legendLabel}</p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default DataAnalysisBarSlide;
