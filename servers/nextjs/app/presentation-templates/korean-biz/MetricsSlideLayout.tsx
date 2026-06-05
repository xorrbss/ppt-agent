import React from 'react'
import * as z from "zod";

export const layoutId = 'korean-biz-metrics'
export const layoutName = '주요 지표'
export const layoutDescription = '제목과 3~4개의 지표 카드(큰 수치 + 라벨)로 구성된 한국형 KPI/지표 슬라이드'

export const Schema = z.object({
  title: z
    .string()
    .min(3)
    .max(40)
    .default('주요 성과 지표')
    .meta({ description: "Slide title summarizing the key performance metrics" }),
  metrics: z
    .array(
      z.object({
        value: z
          .string()
          .min(1)
          .max(12)
          .default('120%')
          .meta({ description: "Large headline metric value (e.g. percentage, currency amount, count)" }),
        label: z
          .string()
          .min(2)
          .max(30)
          .default('매출 성장률')
          .meta({ description: "Short label describing what the metric value represents" }),
      })
    )
    .min(3)
    .max(4)
    .default([
      { value: '₩52억', label: '연간 매출' },
      { value: '98%', label: '고객 만족도' },
      { value: '24개국', label: '진출 시장' },
    ])
    .meta({ description: "Row of 3-4 key metric cards, each with a big value and a label" }),
})

export type MetricsData = z.infer<typeof Schema>

const MetricsSlideLayout: React.FC<{ data?: Partial<MetricsData> }> = ({ data: slideData }) => {
  const metrics = slideData?.metrics && slideData.metrics.length > 0
    ? slideData.metrics
    : [
        { value: '₩52억', label: '연간 매출' },
        { value: '98%', label: '고객 만족도' },
        { value: '24개국', label: '진출 시장' },
      ]

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
      >
        <div className="flex flex-col h-full w-full px-20 py-16">
          {/* Title */}
          <div className="mb-14">
            <div
              className="w-16 h-1.5 rounded-full mb-6"
              style={{ background: "var(--primary-color,#2563eb)" }}
            />
            <h1
              className="text-4xl font-black tracking-tight leading-tight"
              style={{ color: "var(--background-text,#1a1a2e)" }}
            >
              {slideData?.title || '주요 성과 지표'}
            </h1>
          </div>

          {/* Metric cards */}
          <div className="flex-1 grid gap-8 items-stretch" style={{ gridTemplateColumns: `repeat(${metrics.length}, minmax(0, 1fr))` }}>
            {metrics.map((metric, index) => (
              <div
                key={index}
                className="flex flex-col justify-center items-start rounded-2xl border px-10 py-12"
                style={{ backgroundColor: "var(--card-color,#ffffff)", borderColor: "var(--stroke,#e5e7eb)" }}
              >
                <div
                  className="text-6xl font-black leading-none tracking-tight mb-5"
                  style={{ color: "var(--primary-color,#2563eb)" }}
                >
                  {metric?.value || '120%'}
                </div>
                <div
                  className="w-10 h-0.5 rounded-full mb-5 opacity-40"
                  style={{ background: "var(--background-text,#1a1a2e)" }}
                />
                <div
                  className="text-lg font-medium opacity-70"
                  style={{ color: "var(--background-text,#1a1a2e)" }}
                >
                  {metric?.label || '매출 성장률'}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}

export default MetricsSlideLayout
