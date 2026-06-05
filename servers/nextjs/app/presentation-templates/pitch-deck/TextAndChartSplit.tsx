"use client";

import * as z from "zod";

import PitchDeckChart from "./PitchDeckChart";
import { ChartPayloadSchema } from "./pitchDeckSchemas";

export const slideLayoutId = "text-and-chart-split-layout";
export const slideLayoutName = "텍스트와 차트 분할 레이아웃";
export const slideLayoutDescription =
  "왼쪽에 설명 텍스트, 오른쪽에 설정 가능한 차트 영역을 배치한 분할 레이아웃입니다.";

const DEFAULT_CHART = {
  chartType: "bar" as const,
  legendLabel: "시리즈 라벨",
  yAxisLabel: "Y축 이름",
  barData: [
    { label: "월", value: 120 },
    { label: "화", value: 200 },
    { label: "수", value: 150 },
    { label: "목", value: 80 },
    { label: "금", value: 70 },
    { label: "토", value: 110 },
    { label: "일", value: 130 },
  ],
  pieData: [
    { label: "카테고리 A", value: 55, color: "#d8d4bf" },
    { label: "카테고리 B", value: 25, color: "#b8b4a3" },
    { label: "카테고리 C", value: 20, color: "#a2a091" },
  ],
  scatterData: [
    { label: "라벨", value: 7 },
    { label: "라벨", value: 2 },
    { label: "라벨", value: 92 },
    { label: "라벨", value: 15 },
    { label: "라벨", value: 91 },
    { label: "라벨", value: 73 },
    { label: "라벨", value: 56 },
    { label: "라벨", value: 90 },
  ],
  lineData: [
    { label: "월", value: 30 },
    { label: "화", value: 48 },
    { label: "수", value: 64 },
    { label: "목", value: 42 },
    { label: "금", value: 58 },
    { label: "토", value: 70 },
    { label: "일", value: 90 },
  ],
  stackedBarData: [
    { label: "월", value: 50, value2: 50 },
    { label: "화", value: 80, value2: 70 },
    { label: "수", value: 90, value2: 90 },
    { label: "목", value: 40, value2: 60 },
    { label: "금", value: 80, value2: 70 },
    { label: "토", value: 90, value2: 90 },
    { label: "일", value: 70, value2: 80 },
  ],
};

export const Schema = z.object({
  title: z.string().max(16).default("하이라이트").meta({
    description: "Main heading on the left.",
  }),
  leadText: z
    .string()
    .max(52)
    .default("여기에 청중에게 전달할 이야기를 담은 예시 문구를 입력하세요")
    .meta({
      description: "Primary narrative line above supporting text.",
    }),
  supportingText: z
    .string()

    .max(126)
    .default(
      "여기에 청중에게 전달할 핵심 메시지를 입력하세요. 이 영역에는 보조 설명과 세부 내용을 자유롭게 작성할 수 있습니다."
    )
    .meta({
      description: "Supporting paragraph text.",
    }),
  chart: ChartPayloadSchema.default(DEFAULT_CHART).meta({
    description: "Chart configuration payload rendered on the right side.",
  }),
  showAccentGlow: z.boolean().default(true).meta({
    description:
      "Whether to render the subtle decorative glow near bottom-left.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const TextAndChartSplit = ({ data }: { data: Partial<SchemaType> }) => {
  const slideData = data as SchemaType;

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap"
        rel="stylesheet"
      />

      <div
        className="relative h-[720px] w-[1280px] overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#27292d)",
          fontFamily: "var(--body-font-family,'DM Serif Display')",
        }}
      >
        <div className="grid h-full grid-cols-[47.5%_52.5%]">
          <div className="px-[36px] pt-[44px]">
            <h2
              className="font-serif text-[100px] leading-none tracking-[-0.02em]"
              style={{
                color: "var(--background-text,#dddac7)",
              }}
            >
              {slideData.title}
            </h2>

            <p
              className="mt-[76px] max-w-[520px] text-[32px] leading-[1.12]"
              style={{ color: "var(--background-text,#d7d3be)" }}
            >
              {slideData.leadText}
            </p>

            <p
              className="mt-[38px] max-w-[530px] text-[22px] leading-[1.16]"
              style={{ color: "var(--background-text,#cbc7b2)" }}
            >
              {slideData.supportingText}
            </p>
          </div>

          <div className="h-full min-h-0 overflow-hidden px-[24px] pb-[52px] pt-[142px]">
            <PitchDeckChart payload={slideData.chart ?? DEFAULT_CHART} />
          </div>
        </div>
      </div>
    </>
  );
};

export default TextAndChartSplit;
