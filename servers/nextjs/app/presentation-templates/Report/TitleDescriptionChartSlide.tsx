"use client";

import * as z from "zod";

import { FlexibleReportChart, flexibleChartDataSchema } from "./flexibleReportChart";
import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";

export const slideLayoutId = "title-description-chart-slide";
export const slideLayoutName = "제목·설명·차트 슬라이드";
export const slideLayoutDescription =
  "상단에 제목이 있고, 왼쪽에 설명 텍스트, 오른쪽에 차트가 배치된 슬라이드.";


export const Schema = z.object({
  title: z.string().min(3).max(80).default("데이터 분석").meta({
    description: "Slide title shown at the top-left.",
  }),
  insightIcon: z
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
      description: "Icon shown in the featured insight badge.",
    }),
  insightBody: z
    .string()
    .min(30)
    .max(320)
    .default(
      "여기에 본문 내용을 입력하세요. 슬라이드의 핵심 메시지를 설명하는 문단으로, 데이터의 의미와 시사점을 간결하게 정리하여 청중이 한눈에 이해할 수 있도록 작성합니다. 발표 주제에 맞게 자유롭게 수정할 수 있습니다."
    )
    .meta({
      description: "Description text shown in the left content area.",
    }),
  chartData: flexibleChartDataSchema.default({
    type: "line-dual",
    data: [
      { name: "1분기", value: 45 },
      { name: "2분기", value: 72 },
      { name: "3분기", value: 58 },
      { name: "4분기", value: 89 },
    ],

  }),
  legendLabel: z.string().min(3).max(32).default("기존 워크플로").meta({
    description: "Legend label shown below the chart.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const DataAnalysisInsightBarSlide = ({
  data,
}: {
  data: Partial<SchemaType>;
}) => {
  const chartData = data?.chartData?.data ?? [];
  const chartType = data?.chartData?.type ?? "bar";
  const series = data?.chartData?.series ?? [];

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
          className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
          style={{ height: 185, backgroundColor: "var(--primary-color,#157CFF)" }}
        />

        <div className="px-[64px] pt-[48px]">
          <h2
            className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {data.title}
          </h2>
        </div>

        <div className="flex justify-between px-[74px] gap-10 pt-[96px]">
          <div className=" pt-[24px] w-1/2">
            <div className="flex items-center gap-[14px]">
              <div
                className="flex h-[55px] w-[55px] items-center justify-center rounded-full bg-[#157CFF] text-white"
                style={{
                  backgroundColor: "var(--primary-color,#157CFF)",
                  color: "var(--primary-text,#ffffff)",
                }}
              >

                <RemoteSvgIcon
                  url={data.insightIcon?.__icon_url__}
                  strokeColor={"currentColor"}
                  className="h-[25px] w-[25px] object-contain"
                  color="var(--primary-text, #ffffff)"
                  title={data.insightIcon?.__icon_query__}
                />
                {/* <img
                  src={data.insightIcon?.__icon_url__}
                  alt={data.insightIcon?.__icon_query__}
                  className="h-[25px] w-[25px] object-contain"
                  style={{ filter: "invert(1)" }}
                /> */}
              </div>
            </div>
            <p
              className="mt-[20px] text-[24px] leading-[26.667px] text-[#232223]"
              style={{ color: "var(--background-text,#232223)" }}
            >
              {data.insightBody}
            </p>
          </div>

          <div className="ml-[28px] flex w-1/2 flex-col items-center">
            <div className="h-[400px] min-h-0 w-full overflow-hidden">
              <FlexibleReportChart chartType={chartType} data={chartData} series={series} colorFallback="#157CFF" />
            </div>
            <div
              className="mt-[12px] flex items-center gap-[10px] text-[24px] tracking-[-0.03em] text-[#157CFF]"
              style={{ color: "var(--primary-color,#157CFF)" }}
            >
              <span
                className="h-[12px] w-[12px] rounded-full bg-[#157CFF]"
                style={{ backgroundColor: "var(--primary-color,#157CFF)" }}
              />
              <p>{data.legendLabel}</p>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default DataAnalysisInsightBarSlide;
