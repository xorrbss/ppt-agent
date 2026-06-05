import React from "react"
import * as z from "zod"

const layoutId = "MetricsNumbers"
const layoutName = "지표 수치"
const layoutDescription = "Swift: 세 개의 누적 지표 카드로 표현한 숫자로 보는 우리의 성과"

const MetricSchema = z
  .object({
    value: z.string().min(1).max(8).default("10K+"),
    line1: z.string().min(2).max(22).default("전체"),
    line2: z.string().min(0).max(22).default("사용자"),
    description: z
      .string()
      .min(10)
      .max(140)
      .default("여러 산업에 걸친 활성 사용자"),
  })
  .default({
    value: "10K+",
    line1: "전체",
    line2: "사용자",
    description: "여러 산업에 걸친 활성 사용자",
  })

const Schema = z
  .object({
    title: z
      .string()
      .min(8)
      .max(60)
      .default("숫자로 보는 우리의 성과"),
    leftTitle: z
      .string()
      .min(6)
      .max(40)
      .default("데이터로 입증된\n성과"),
    leftBody: z
      .string()
      .min(30)
      .max(220)
      .default(
        "데이터를 바탕으로 거둔 성과를 간결하게 소개하는 설명 문장입니다."
      ),
    website: z.string().min(6).max(60).default("www.yourwebsite.com"),
    metrics: z
      .array(MetricSchema)
      .min(1)
      .max(4)
      .default([
        MetricSchema.parse({
          value: "10K+",
          line1: "전체",
          line2: "사용자",
          description: "여러 산업에 걸친 활성 사용자",
        }),
        MetricSchema.parse({
          value: "150%",
          line1: "매출",
          line2: "성장",
          description: "전년 대비 매출 성장",
        }),
        MetricSchema.parse({
          value: "95%",
          line1: "고객",
          line2: "만족도",
          description: "평균 4.8/5점 평가를 기록한 고객 유지율",
        }),
      ]),
  })
  .default({
    title: "숫자로 보는 우리의 성과",
    leftTitle: "데이터로 입증된\n성과",
    leftBody: "데이터를 바탕으로 거둔 성과를 간결하게 소개하는 설명 문장입니다.",
    website: "www.yourwebsite.com",
    metrics: [
      MetricSchema.parse({
        value: "10K+",
        line1: "전체",
        line2: "사용자",
        description: "여러 산업에 걸친 활성 사용자",
      }),
      MetricSchema.parse({
        value: "150%",
        line1: "매출",
        line2: "성장",
        description: "전년 대비 매출 성장",
      }),
      MetricSchema.parse({
        value: "95%",
        line1: "고객",
        line2: "만족도",
        description: "평균 4.8/5점 평가를 기록한 고객 유지율",
      }),
    ],
  })

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const MetricsNumbers: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
  const metrics = slideData?.metrics || []
  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />

      <div
        className=" w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{
          fontFamily: "var(--heading-font-family,Albert Sans)",
          backgroundColor: "var(--background-color, #FFFFFF)",
        }}
      >
        {/* Header */}
        <div className="px-12 pt-6 pb-2">
          <div className="flex items-center gap-3">
            <div className="w-3 h-3 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
            <div className="flex items-center gap-1">

              {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
              {(slideData as any)?.__companyName__ && <span className="text-[16px]" style={{ color: "var(--background-text, #6B7280)" }}>{(slideData as any)?.__companyName__}</span>}
            </div>
          </div>
        </div>

        {/* Separator line like the reference */}
        <div className="absolute top-0 left-1/2 w-[1px] h-full" style={{ backgroundColor: "rgba(0,0,0,0.1)" }}></div>

        <div className="px-12 pt-3 grid grid-cols-[42%_58%] gap-8 items-start">
          {/* Left content */}
          <div>
            <h1 className="text-[48px] leading-[1.1] font-semibold max-w-[420px]" style={{ color: "var(--background-text, #111827)" }}>{slideData?.title}</h1>
            <div className="mt-8 inline-flex items-center gap-3">
              <div className="w-5 h-5 rounded-full" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
              <div>
                <div className="text-[20px] font-semibold whitespace-pre-line" style={{ color: "var(--background-text, #111827)" }}>{slideData?.leftTitle}</div>
              </div>
            </div>
            <p className="mt-5 text-[16px] leading-[1.8] max-w-[360px]" style={{ color: "var(--background-text, #6B7280)" }}>{slideData?.leftBody}</p>
          </div>

          {/* Right stacked metric cards */}
          <div className="relative">
            {/* decorative circle on the right */}
            <div className="absolute top-6 -right-24 w-[220px] h-[220px] rounded-full border" style={{ borderColor: "rgba(0,0,0,0.2)" }}></div>

            <div className="flex flex-col gap-6">
              {metrics.slice(0, 3).map((m, i) => (
                <div key={i} className="rounded-[18px] px-6 py-5 grid grid-cols-[38%_62%] items-start shadow-[0_16px_40px_rgba(0,0,0,0.08)]" style={{ backgroundColor: 'var(--primary-color, #BFF4FF)' }}>
                  <div className="text-[40px] font-semibold" style={{ color: 'var(--primary-text, #111827)' }}>{m.value}</div>
                  <div>
                    <div className="text-[16px] font-semibold" style={{ color: 'var(--primary-text, #111827)' }}>{m.line1}</div>
                    {m.line2 && <div className="-mt-1 text-[16px] font-semibold" style={{ color: 'var(--primary-text, #111827)' }}>{m.line2}</div>}
                    <p className="mt-3 text-[12px] leading-[1.6]" style={{ color: 'var(--primary-text, #6B7280)' }}>{m.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Footer (standardized like IntroSlideLayout) */}
        <div className="absolute bottom-8 left-12 right-12 flex items-center">
          <span className="text-[14px]" style={{ color: "var(--background-text, #6B7280)" }}>{slideData?.website}</span>
          <div className="ml-6 h-[2px] flex-1" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
        </div>
        <div className="absolute bottom-7 right-6 w-8 h-8 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
      </div>
    </>
  )
}

export { Schema, layoutId, layoutName, layoutDescription }
export default MetricsNumbers


