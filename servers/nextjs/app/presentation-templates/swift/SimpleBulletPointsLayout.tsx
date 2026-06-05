import React from "react"
import * as z from "zod"

const layoutId = "simple-bullet-points-layout"
const layoutName = "간단한 글머리"
const layoutDescription = "제목과 설명이 있는 글머리"

const PointSchema = z
  .object({
    title: z.string().min(6).max(60).default("여기에 제목 입력"),
    body: z
      .string()
      .min(30)
      .max(220)
      .default(
        "여기에 항목을 설명하는 본문을 입력하세요. 핵심 내용을 간결하고 명확하게 작성합니다."
      ),
  })
  .default({ title: "여기에 제목 입력", body: "여기에 항목을 설명하는 본문을 입력하세요. 핵심 내용을 간결하고 명확하게 작성합니다." })

const Schema = z
  .object({
    title: z.string().min(4).max(36).default("우리의 약속"),
    statement: z
      .string()
      .min(20)
      .max(260)
      .default(
        "우리가 추구하는 가치를 간결하게 전달하는 설명 문장입니다."
      ),
    points: z
      .array(PointSchema)
      .min(1)
      .max(4)
      .default([PointSchema.parse({}), PointSchema.parse({}), PointSchema.parse({}), PointSchema.parse({ title: "여기에 제목 입력", body: "여기에 항목을 설명하는 본문을 입력하세요. 핵심 내용을 간결하고 명확하게 작성합니다." })]),
    website: z.string().min(6).max(60).default("www.yourwebsite.com"),
  })
  .default({
    title: "혁신을 향한 우리의 약속",
    statement: "우리가 추구하는 가치를 간결하게 전달하는 설명 문장입니다.",
    points: [
      { title: "여기에 제목 입력", body: "여기에 항목을 설명하는 본문을 입력하세요. 핵심 내용을 간결하고 명확하게 작성합니다." },
      { title: "여기에 제목 입력", body: "여기에 항목을 설명하는 본문을 입력하세요. 핵심 내용을 간결하고 명확하게 작성합니다." },
      { title: "여기에 제목 입력", body: "여기에 항목을 설명하는 본문을 입력하세요. 핵심 내용을 간결하고 명확하게 작성합니다." },
      { title: "여기에 제목 입력", body: "여기에 항목을 설명하는 본문을 입력하세요. 핵심 내용을 간결하고 명확하게 작성합니다." },

    ],
    website: "www.yourwebsite.com",
  })

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const CommitmentTwoPoints: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
  const points = slideData?.points || []
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

        {/* Subtle background motif */}
        <div
          className="pointer-events-none absolute left-1/2 -translate-x-1/2 bottom-[140px] w-[900px] h-[260px]"
          style={{
            background:
              "radial-gradient(closest-side, rgba(17,24,39,0.06), transparent 70%)",
            filter: "blur(0.2px)",
          }}
        ></div>

        {/* Content grid */}
        <div className="px-12 pt-4 grid grid-cols-[48%_52%] gap-10 items-start">
          {/* Left heading and statement */}
          <div>
            <div className="text-[56px] leading-[1.05] font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
              {slideData?.title}
            </div>

            <p className="mt-10 text-[16px] leading-[1.8] max-w-[520px]" style={{ color: 'var(--background-text, #6B7280)' }}>
              {slideData?.statement}
            </p>
          </div>

          {/* Right numbered points (up to 4) */}
          <div className="flex flex-col gap-6">
            {points.slice(0, 4).map((p, i) => (
              <div key={i}>
                <div className="text-[24px] font-semibold" style={{ color: 'var(--background-text, #111827)' }}>{p.title}</div>
                <p className="mt-3 text-[16px] leading-[1.8]" style={{ color: 'var(--background-text, #6B7280)' }}>{p.body}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Footer (align with other Swift layouts) */}
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
export default CommitmentTwoPoints


