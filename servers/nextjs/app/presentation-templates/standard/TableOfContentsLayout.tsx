import React from "react"
import * as z from "zod"

const layoutId = "table-of-contents-layout"
const layoutName = "목차"
const layoutDescription = "브랜드 마커가 있는 헤더, 제목, 선택적 설명, 그리고 2열 목차 목록으로 구성된 슬라이드"

const ToCItemSchema = z
  .object({
    title: z.string().min(4).max(50).default("소개").meta({
      description: "Section title. Max 50 characters",
    }),
  })
  .default({
    title: "소개",
  })

const Schema = z
  .object({
    topBar: z
      .object({

        marker: z.string().min(1).max(3).default("2").meta({
          description: "Numeric marker on the top bar. Up to 3 digits",
        }),
      })
      .default({ marker: "2" }),

    title: z
      .string()
      .min(12)
      .max(68)
      .default("목차")
      .meta({ description: "Main slide title. Max 10 words" }),

    description: z
      .string()
      .min(0)
      .max(200)
      .default(
        "프레젠테이션의 각 섹션을 빠르게 살펴볼 수 있는 안내로 활용하세요."
      )
      .meta({ description: "Lead paragraph. Optional. Max 35 words" }),

    items: z
      .array(ToCItemSchema)
      .min(3)
      .max(10)
      .default([
        { title: "소개" },
        { title: "문제 정의" },
        { title: "해결책" },
        { title: "시장" },
        { title: "비즈니스 모델" },
        { title: "로드맵" },
        { title: "팀" },
        { title: "시장 진출 전략" },
        { title: "재무" },
        { title: "투자 요청" },
      ])
      .meta({ description: "List of contents (3-10)" }),
  })
  .default({
    topBar: { marker: "2" },
    title: "목차",
    description:
      "프레젠테이션의 각 섹션을 빠르게 살펴볼 수 있는 안내로 활용하세요.",
    items: [
      { title: "소개" },
      { title: "문제 정의" },
      { title: "해결책" },
      { title: "시장" },
      { title: "비즈니스 모델" },
      { title: "로드맵" },
      { title: "팀" },
      { title: "시장 진출 전략" },
      { title: "재무" },
      { title: "투자 요청" },
    ],
  })

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const dynamicSlideLayout: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
  const items = slideData?.items || []

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />

      <div
        className=" w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{
          fontFamily: "var(--heading-font-family,Playfair Display)",
          backgroundColor: "var(--background-color, #FFFFFF)",
        }}
      >
        <div className="px-12 pt-6 pb-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-6">
              <div className="flex items-center gap-1">

                {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
                {(slideData as any)?.__companyName__ && <span className="text-[18px]  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>{(slideData as any)?.__companyName__ || "Pitchdeck"}</span>}
              </div>
              <div
                className="h-[2px] w-[220px]"
                style={{ backgroundColor: "var(--background-text, #111827)" }}
              ></div>
            </div>
          </div>
        </div>

        <div className="px-12">
          <h1
            className="text-[64px] leading-[1.05] tracking-tight  font-semibold mt-2"
            style={{ color: "var(--background-text, #111827)" }}
          >
            {slideData?.title}
          </h1>
          {slideData?.description && (
            <p
              className="mt-5 text-[16px] leading-[1.6] max-w-[1020px] "
              style={{ color: "var(--background-text, #6B7280)" }}
            >
              {slideData?.description}
            </p>
          )}
        </div>

        <div className="px-10 mt-10">
          <div className="grid grid-cols-2 gap-4">
            {items.map((item, idx) => (
              <div
                key={idx}
                className="rounded-sm border shadow-[0_8px_24px_rgba(0,0,0,0.06)] px-4 py-3 flex items-center gap-4"
                style={{
                  backgroundColor: "var(--card-color, #FFFFFF)",
                  borderColor: "var(--stroke, #E5E7EB)",
                }}
              >
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center text-[16px] font-semibold"
                  style={{
                    border: "2px solid var(--primary-color, #1B8C2D)",
                    color: "var(--background-text, #111827)",
                  }}
                >
                  {idx + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <div
                    className="text-[18px] leading-tight font-semibold  truncate"
                    style={{ color: "var(--background-text, #111827)" }}
                  >
                    {item.title}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}

export { Schema, layoutId, layoutName, layoutDescription }
export default dynamicSlideLayout


