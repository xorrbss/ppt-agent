import React from 'react'
// charts removed
import * as z from "zod";

const ImageSchema = z.object({
  __image_url__: z.string().url().default("https://images.unsplash.com/photo-1503264116251-35a269479413?q=80&w=1200&auto=format&fit=crop").meta({
    description: "URL to image",
  }),
  __image_prompt__: z.string().min(10).max(200).default("Elegant abstract green themed background for a presentation slide, minimal shapes, soft lighting").meta({
    description: "Prompt used to generate the image. Max 40 words",
  }),
})

const IconSchema = z.object({
  __icon_url__: z.string().default("https://static.thenounproject.com/png/1783767-200.png").meta({
    description: "URL to icon",
  }),
  __icon_query__: z.string().min(3).max(40).default("leaf growth").meta({
    description: "Query used to search the icon. Max 6 words",
  }),
})

const layoutId = "header-tagline-cards-grid-slide"
const layoutName = "지표 설명"
const layoutDescription = "상단 도구 행, 헤더, 태그라인, 그리고 각각 숫자 블록과 텍스트가 있는 카드 그리드로 구성된 슬라이드"

const CardSchema = z.object({
  number: z.string().min(1).max(5).default("45").meta({
    description: "Main number text inside number block. 1 to 3 digits",
  }),
  numberSymbol: z.string().min(0).max(3).default("%").meta({
    description: "Optional symbol next to the number. Single character",
  }),
  subtitle: z.string().min(8).max(28).default("부제목 입력").meta({
    description: "Card subtitle. Max 5 words",
  }),
  body: z.string().min(20).max(100).default("여기에 카드 내용을 설명하는 예시 문구를 입력하세요.").meta({
    description: "Card body text. Max 100 characters",
  }),
  icon: IconSchema.default({
    __icon_url__: "https://static.thenounproject.com/png/1783767-200.png",
    __icon_query__: "progress indicator",
  }).meta({
    description: "Optional icon for the card header area",
  }),
})

const Schema = z.object({

  title: z.string().min(12).max(70).default("함께 새로운 정상을 향해").meta({
    description: "Main title. Single line up to ~34 chars or two lines up to ~70 chars. Max 9 words",
  }),
  tagline: z.string().min(40).max(120).default("여기에 제목을 뒷받침하는 부제목 예시 문구를 입력하세요. 슬라이드의 메시지를 간결하게 전달하세요.").meta({
    description: "Subtitle/tagline under title. Max 20 words",
  }),
  decorativeLine: ImageSchema.default({
    __image_url__: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='2' viewBox='0 0 220 2'><rect width='220' height='2' rx='1' fill='%230B8E26'/></svg>",
    __image_prompt__: "Thin green horizontal line divider, 220x2, rounded ends",
  }).meta({
    description: "SVG decorative line asset",
  }),
  cards: z.array(CardSchema).min(1).max(6).default([
    {
      number: "87",
      numberSymbol: "%",
      subtitle: "고객 만족도",
      body: "고객들은 우리 제품과 서비스 경험을 한결같이 훌륭하다고 평가합니다.",
      icon: { __icon_url__: "https://static.thenounproject.com/png/1783767-200.png", __icon_query__: "happy customer icon" },
    },
    {
      number: "2.5",
      numberSymbol: "M",
      subtitle: "월간 활성 사용자",
      body: "전 세계 여러 지역에서 플랫폼을 활발히 이용하는 사용자 기반이 성장하고 있습니다.",
      icon: { __icon_url__: "https://static.thenounproject.com/png/1783767-200.png", __icon_query__: "users group icon" },
    },
    {
      number: "99",
      numberSymbol: "%",
      subtitle: "시스템 가동률",
      body: "업계 최고 수준의 시스템 가용성과 성능으로 뛰어난 안정성을 유지합니다.",
      icon: { __icon_url__: "https://static.thenounproject.com/png/1783767-200.png", __icon_query__: "server uptime icon" },
    },
    {
      number: "142",
      numberSymbol: "+",
      subtitle: "글로벌 파트너",
      body: "주요 산업 분야 전반에서 혁신과 시장 확장을 이끄는 전략적 파트너십입니다.",
      icon: { __icon_url__: "https://static.thenounproject.com/png/1783767-200.png", __icon_query__: "handshake deal icon" },
    },
    {
      number: "32",
      numberSymbol: "x",
      subtitle: "매출 성장",
      body: "탄탄한 시장 지위와 비즈니스 모델의 확장성을 입증하는 전년 대비 성장입니다.",
      icon: { __icon_url__: "https://static.thenounproject.com/png/1783767-200.png", __icon_query__: "growth chart icon" },
    },
    {
      number: "500",
      numberSymbol: "K",
      subtitle: "탄소 상쇄",
      body: "상당한 탄소 감축과 환경 이니셔티브를 통해 지속가능성에 헌신합니다.",
      icon: { __icon_url__: "https://static.thenounproject.com/png/1783767-200.png", __icon_query__: "leaf sustainability icon" },
    },
  ]).meta({
    description: "Grid of cards with number block, subtitle, and body (<=100 chars)",
  }),
  // chart and diagram removed
})

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const dynamicSlideLayout: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
  const cards = slideData?.cards || []

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />
      <div className=" w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden" style={{ fontFamily: "var(--heading-font-family,Playfair Display)", backgroundColor: 'var(--background-color, #FFFFFF)' }}>
        <div className="h-full flex flex-col px-10 pt-6 pb-8">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1">

                {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
                {(slideData as any)?.__companyName__ && <span className="text-[18px]  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>{(slideData as any)?.__companyName__ || "Pitchdeck"}</span>}
              </div>
              <div className="block w-[220px] h-[2px]" style={{ backgroundColor: 'var(--background-text, #111827)' }}></div>
            </div>
            {/* page number removed */}
          </div>

          <h1 className="mt-4 text-[64px] leading-[1.06] tracking-tight  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
            {slideData?.title || "함께 새로운 정상을 향해"}
          </h1>

          <p className="mt-3 text-[16px] " style={{ color: 'var(--background-text, #6B7280)' }}>
            {slideData?.tagline || "여기에 제목을 뒷받침하는 부제목 예시 문구를 입력하세요. 슬라이드의 메시지를 간결하게 전달하세요."}
          </p>

          <div className="mt-8 grid grid-cols-2 gap-x-10 gap-y-6">
            {cards.map((card, idx) => (
              <div key={idx} className="rounded-md shadow-sm px-5 py-4" style={{ backgroundColor: 'var(--primary-color, #1B8C2D)', color: 'var(--primary-text, #FFFFFF)' }}>
                <div className="flex items-start gap-4">
                  <div className="flex items-baseline shrink-0">
                    <span className="text-white  text-[48px] leading-none" style={{ color: 'var(--primary-text, #FFFFFF)' }}>
                      {card.number}
                    </span>
                    <span className="ml-1 text-white  text-[24px] leading-none" style={{ color: 'var(--primary-text, #FFFFFF)' }}>
                      {card.numberSymbol}
                    </span>
                  </div>
                  <div className="flex-1">
                    <h3 className="text-white  text-[24px]" style={{ color: 'var(--primary-text, #FFFFFF)' }}>
                      {card.subtitle}
                    </h3>
                    <p className="mt-1 text-white/95  text-[16px] leading-[1.55]" style={{ color: 'var(--primary-text, #FFFFFF)' }}  >
                      {card.body}
                    </p>
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