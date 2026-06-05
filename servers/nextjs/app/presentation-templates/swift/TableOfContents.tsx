import React from "react"
import * as z from "zod"

const layoutId = "SwiftTableOfContents"
const layoutName = "목차"
const layoutDescription = "Swift: 최대 10개 항목(제목 + 설명)을 담은 목차"

const ToCItemSchema = z
  .object({
    title: z.string().min(3).max(40).default("소개"),
    description: z
      .string()
      .min(0)
      .max(60)
      .default("섹션에 대한 간단한 개요입니다."),
  })
  .default({ title: "소개", description: "섹션에 대한 간단한 개요입니다." })

const Schema = z
  .object({
    title: z
      .string()
      .min(3)
      .max(60)
      .default("목차"),
    items: z
      .array(ToCItemSchema)
      .min(1)
      .max(10)
      .default([
        { title: "소개", description: "회사와 목표를 간단히 소개하는 설명입니다." },
        { title: "우리 팀", description: "리더십과 핵심 구성원입니다." },
        { title: "타임라인", description: "주요 실행 계획과 마일스톤입니다." },
        { title: "권장 사항", description: "초기 요구사항을 바탕으로 한 핵심 제안입니다." },
        { title: "솔루션", description: "우리가 제안하는 내용과 그 이유입니다." },
        { title: "시장", description: "대상 고객, 세그먼트, 기회 규모입니다." },
        { title: "비즈니스 모델", description: "가치를 창출하고 확보하는 방식입니다." },
        { title: "마무리", description: "마무리 요약과 다음 단계입니다." },
        { title: "비즈니스 모델", description: "가치를 창출하고 확보하는 방식입니다." },
        { title: "마무리", description: "마무리 요약과 다음 단계입니다." },
      ]),
    website: z.string().min(6).max(60).default("www.yourwebsite.com"),
  })
  .default({
    title: "목차",
    items: [
      { title: "소개", description: "회사와 목표를 간단히 소개하는 설명입니다." },
      { title: "우리 팀", description: "리더십과 핵심 구성원입니다." },
      { title: "타임라인", description: "주요 실행 계획과 마일스톤입니다." },
      { title: "권장 사항", description: "초기 요구사항을 바탕으로 한 핵심 제안입니다." },
      { title: "솔루션", description: "우리가 제안하는 내용과 그 이유입니다." },
      { title: "시장", description: "대상 고객, 세그먼트, 기회 규모입니다." },
      { title: "비즈니스 모델", description: "가치를 창출하고 확보하는 방식입니다." },
      { title: "마무리", description: "마무리 요약과 다음 단계입니다." },
      { title: "비즈니스 모델", description: "가치를 창출하고 확보하는 방식입니다." },
      { title: "마무리", description: "마무리 요약과 다음 단계입니다." },

    ],
    website: "www.yourwebsite.com",
  })

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const TableOfContents: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
  const items = slideData?.items || []
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

        <div className="px-12 pt-3">
          <h1 className="text-[48px] leading-[1.1] font-semibold" style={{ color: "var(--background-text, #111827)" }}>{slideData?.title}</h1>
        </div>

        {/* List */}
        <div className="px-12 pt-8">
          <div className="grid grid-cols-2 gap-x-12 gap-y-6 max-w-[1180px]">
            {items.slice(0, 10).map((item, idx) => (
              <div key={idx} className="relative">
                <div className="flex items-start gap-6">
                  <div className="flex-none">
                    <div
                      className="leading-none font-semibold"
                      style={{
                        fontSize: 48,
                        color: "var(--primary-color, #BFF4FF)",
                      }}
                    >
                      {String(idx + 1).padStart(2, "0")}
                    </div>
                  </div>
                  <div className="min-w-0 flex-1 pt-1">
                    <div className="text-[22px] leading-[1.2] font-semibold" style={{ color: "var(--background-text, #111827)" }}>
                      {item.title}
                    </div>
                    {item.description && (
                      <div className="mt-2 text-[14px] leading-[1.6]" style={{ color: "var(--background-text, #6B7280)" }}>
                        {item.description}
                      </div>
                    )}
                  </div>
                </div>
                <div className="mt-4 h-px" style={{ backgroundColor: "var(--stroke, #E5E7EB)" }}></div>
              </div>
            ))}
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
export default TableOfContents


