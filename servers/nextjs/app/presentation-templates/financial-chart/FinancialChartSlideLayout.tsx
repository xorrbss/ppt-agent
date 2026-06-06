"use client";

import React from 'react'
import * as z from "zod";
import { GeneralChart } from '../general/GeneralChartPrimitives';

export const layoutId = 'financial-chart-slide'
export const layoutName = '재무·실적 차트'
export const layoutDescription = '제목, 막대/선/영역 차트와 핵심 수치 카드로 구성된 한국형 재무·실적 슬라이드. 매출·이익 등 시계열 수치 데이터가 있을 때 선택하세요.'

// Single source of truth for sample data: used as both the Zod default and
// the component fallback when no data is provided.
const DEFAULT_CHART = {
  type: 'bar' as const,
  data: [
    { name: '2021', value: 320 },
    { name: '2022', value: 410 },
    { name: '2023', value: 480 },
    { name: '2024', value: 560 },
    { name: '2025', value: 690 },
  ],
}

const DEFAULT_HIGHLIGHTS = [
  { value: '₩690억', label: '2025 매출', caption: '전년 대비 +23%' },
  { value: '18.4%', label: '영업이익률', caption: '3년 연속 개선' },
  { value: 'CAGR 21%', label: '연평균 성장률', caption: '2021–2025' },
]

export const Schema = z.object({
  title: z
    .string()
    .min(3)
    .max(40)
    .default('연간 실적 추이')
    .meta({ description: "Slide title summarizing the financial/performance chart" }),
  chart: z
    .object({
      type: z
        .enum(['bar', 'line', 'area'])
        .default('bar')
        .meta({ description: "Chart type: 'bar', 'line', or 'area'" }),
      data: z
        .array(
          z.object({
            name: z
              .string()
              .min(1)
              .max(12)
              .meta({ description: "X-axis label such as a fiscal year or quarter (e.g. '2024', 'Q1')" }),
            value: z
              .number()
              .meta({ description: "Numeric value for the period (revenue, profit, etc.)" }),
          })
        )
        .min(3)
        .max(7)
        .meta({ description: "Time-series data points, 3 to 7 periods" }),
    })
    .default(DEFAULT_CHART)
    .meta({ description: "Chart type and its time-series data" }),
  highlights: z
    .array(
      z.object({
        value: z
          .string()
          .min(1)
          .max(12)
          .default('₩690억')
          .meta({ description: "Large headline figure (currency, percentage, ratio)" }),
        label: z
          .string()
          .min(2)
          .max(20)
          .default('연간 매출')
          .meta({ description: "Short label describing the figure" }),
        caption: z
          .string()
          .min(2)
          .max(28)
          .default('전년 대비 +23%')
          .meta({ description: "Supporting note such as a year-over-year change" }),
      })
    )
    .min(2)
    .max(3)
    .default(DEFAULT_HIGHLIGHTS)
    .meta({ description: "2-3 key figures shown beside the chart" }),
})

export type FinancialChartData = z.infer<typeof Schema>

const FinancialChartSlideLayout: React.FC<{ data?: Partial<FinancialChartData> }> = ({ data: slideData }) => {
  const title = slideData?.title || '연간 실적 추이'
  const chart = slideData?.chart && slideData.chart.data && slideData.chart.data.length > 0
    ? slideData.chart
    : DEFAULT_CHART
  const highlights = slideData?.highlights && slideData.highlights.length > 0
    ? slideData.highlights
    : DEFAULT_HIGHLIGHTS

  // Force the shared GeneralChart's per-index palette to a single primary color
  // for a conservative, single-series financial look (overrides --graph-N).
  const monoChartVars = Object.fromEntries(
    Array.from({ length: 10 }, (_, i) => [`--graph-${i}`, "var(--primary-color,#2563eb)"] as [string, string])
  ) as React.CSSProperties

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
      >
        <div className="flex flex-col h-full w-full px-20 py-16">
          {/* Title */}
          <div className="flex items-center gap-4 mb-10">
            <div className="w-2 h-12 rounded-full" style={{ background: "var(--primary-color,#2563eb)" }} />
            <h1 className="text-4xl font-black tracking-tight" style={{ color: "var(--background-text,#1a1a2e)" }}>
              {title}
            </h1>
          </div>

          {/* Chart + highlights */}
          <div className="flex-1 flex gap-8 min-h-0">
            {/* Chart card */}
            <div
              className="flex-1 min-h-0 rounded-2xl border p-6"
              style={{ backgroundColor: "var(--card-color,#ffffff)", borderColor: "var(--stroke,#e5e7eb)" }}
            >
              <div className="h-full w-full min-h-0 overflow-hidden" style={monoChartVars}>
                <GeneralChart type={chart.type} data={chart.data} showLegend={false} showTooltip={false} />
              </div>
            </div>

            {/* Highlights */}
            <div className="w-72 flex flex-col gap-5 justify-center">
              {highlights.map((h, i) => (
                <div
                  key={i}
                  className="rounded-2xl border px-7 py-6"
                  style={{ backgroundColor: "var(--card-color,#ffffff)", borderColor: "var(--stroke,#e5e7eb)" }}
                >
                  <div className="text-3xl font-black leading-none tracking-tight mb-3" style={{ color: "var(--primary-color,#2563eb)" }}>
                    {h?.value || '₩690억'}
                  </div>
                  <div className="text-base font-bold mb-1" style={{ color: "var(--background-text,#1a1a2e)" }}>
                    {h?.label || '연간 매출'}
                  </div>
                  <div className="text-sm opacity-60" style={{ color: "var(--background-text,#1a1a2e)" }}>
                    {h?.caption || '전년 대비 +23%'}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

export default FinancialChartSlideLayout
