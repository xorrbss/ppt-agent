import * as z from "zod";

import EducationChartPrimitives, {
  type EducationChartDatum,
  type EducationChartType,
} from "./EducationChartPrimitives";

export const slideLayoutId = "report-chart-slide";
export const slideLayoutName = "보고서 차트 슬라이드";
export const slideLayoutDescription =
  "보고서 제목, 본문, 각주가 담긴 왼쪽 텍스트 열과 오른쪽 차트로 구성된 레이아웃.";

const ChartTypeSchema = z.enum([
  "bar",

  "line",
  "area",

  "pie",
  "donut",
  "scatter",
]);

const SimpleDataSchema = z.object({
  name: z.string().min(1).max(20).meta({
    description: "Simple chart category label.",
  }),
  value: z.number().meta({
    description: "Simple chart numeric value.",
  }),
});

const MultiSeriesDataSchema = z.object({
  name: z.string().min(1).max(20).meta({
    description: "Grouped/stacked category label.",
  }),
  values: z.record(z.string(), z.number()).meta({
    description: "Series-to-value map for grouped or stacked charts.",
  }),
});

const DivergingDataSchema = z.object({
  name: z.string().min(1).max(20).meta({
    description: "Diverging chart category label.",
  }),
  positive: z.number().min(0).max(100000).meta({
    description: "Positive side value.",
  }),
  negative: z.number().min(0).max(100000).meta({
    description: "Negative side value.",
  }),
});

const ScatterDataSchema = z.object({
  x: z.number().min(-100000).max(100000).meta({
    description: "Scatter X coordinate.",
  }),
  y: z.number().min(-100000).max(100000).meta({
    description: "Scatter Y coordinate.",
  }),
  name: z.string().min(1).max(20).optional().meta({
    description: "Optional scatter tick label.",
  }),
});

const UnifiedChartDataSchema = z.union([
  z.array(SimpleDataSchema),
  z.array(MultiSeriesDataSchema),
  z.array(DivergingDataSchema),
  z.array(ScatterDataSchema),
]);

export const Schema = z.object({
  title: z.string().max(24).default("보고서").meta({
    description: "Left-side report title.",
  }),
  body: z
    .string()
    .min(80)
    .max(260)
    .default(
      "이번 분석은 학년별 학생 분포와 학습 성과의 변화를 한눈에 살펴볼 수 있도록 정리한 자료입니다. 주요 지표를 바탕으로 강점과 개선이 필요한 영역을 명확히 파악하여, 앞으로의 교육 방향을 수립하는 데 활용할 수 있습니다."
    )
    .meta({
      description: "Left-side report body paragraph.",
    }),
  footnote: z
    .string()
    .min(20)
    .max(150)
    .default(
      "(본 자료는 2024년 교육 통계 연보를 기준으로 작성되었습니다.)"
    )
    .meta({
      description: "Left-side footnote line.",
    }),
  chartTitle: z
    .string()
    .min(8)
    .max(42)
    .default("학년별 학생 수")
    .meta({
      description: "Right-panel chart heading.",
    }),
  dateRange: z.string().min(8).max(22).default("4월 10일 - 4월 17일").meta({
    description: "Right-panel date range label.",
  }),
  chartType: ChartTypeSchema.default("bar").meta({
    description:
      "Chart type selector. Supports bar, grouped, stacked, clustered, diverging, line, area, pie/donut, and scatter.",
  }),
  chartData: UnifiedChartDataSchema.default([
    { name: "항목 A", value: 17.07 },
    { name: "항목 B", value: 45.23 },
    { name: "항목 C", value: 21.61 },
    { name: "항목 D", value: 16.36 },
  ]).meta({
    description: "Unified chart data payload. Shape depends on chartType.",
  }),
  series: z
    .array(z.string().min(1).max(20))
    .max(6)
    .default(["시리즈 A", "시리즈 B"])
    .meta({
      description:
        "Series names for grouped/stacked/clustered/area-stacked charts.",
    }),
  divergingLabels: z
    .tuple([z.string().min(1).max(24), z.string().min(1).max(24)])
    .default(["긍정", "부정"])
    .meta({
      description: "Legend labels for bar-diverging charts.",
    }),
  showLegend: z.boolean().default(true).meta({
    description: "Show or hide chart legend.",
  }),

  showStatusMessage: z.boolean().default(false).meta({
    description:
      "Show callout message under chart (useful for weekly/performance styles).",
  }),
  statusMessageTitle: z
    .string()
    .min(8)
    .max(40)
    .default("아주 잘하고 있어요!")
    .meta({
      description: "Callout headline under chart.",
    }),
  statusMessageBody: z
    .string()
    .min(10)
    .max(80)
    .default("목표에 거의 다 도달했어요")
    .meta({
      description: "Callout subtitle under chart.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const EducationReportChartSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const slideData = data;

  const chartHeightClass = slideData.showStatusMessage
    ? "h-[372px]"
    : "h-[486px]";

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900&display=swap"
        rel="stylesheet"
      />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden"
        style={{
          backgroundColor: "var(--background-color,#efeff1)",
          fontFamily: "var(--body-font-family,'Times New Roman')",
        }}
      >
        <div className="grid h-full grid-cols-[1fr_560px] items-center ">
          <div className="px-[52px] pb-[46px] mt-[111px]   ">
            <div className="text-start">
              <h2
                className=" text-[64px] font-medium leading-[98%]"
                style={{ color: "var(--primary-color,#101C3D)" }}
              >
                {slideData.title}
              </h2>
              <p
                className=" mt-[38px] max-w-[610px] text-[22px] leading-[1.22]"
                style={{ color: "var(--background-text,#3E3F4A)" }}
              >
                {slideData.body}
              </p>
            </div>

            <p
              className="max-w-[610px] mt-[96px] text-[18px] leading-[1.22]"
              style={{ color: "var(--background-text,#4E4F57)" }}
            >
              {slideData.footnote}
            </p>
          </div>

          <div
            className="px-[42px] h-full flex flex-col justify-center"
            style={{ backgroundColor: "var(--card-color,#eceaf0)" }}
          >
            <h3
              className="text-center  text-[24px] font-semibold leading-none"
              style={{ color: "var(--background-text,#33313A)" }}
            >
              {slideData.chartTitle}
            </h3>
            <p
              className="mt-1 text-center pb-6 text-[18px] leading-none"
              style={{ color: "var(--background-text,#4D4B55)" }}
            >
              {slideData.dateRange}
            </p>

            <div className={`${chartHeightClass} min-h-0 overflow-hidden`}>
              <EducationChartPrimitives
                chartType={slideData.chartType as EducationChartType}
                chartData={slideData.chartData as EducationChartDatum[]}
                series={slideData.series || []}
                showLegend={slideData.showLegend || false}
                divergingLabels={slideData.divergingLabels || ["", ""]}
                showTooltip={true}
              />
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default EducationReportChartSlide;
