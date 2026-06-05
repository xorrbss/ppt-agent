import React from 'react'
// charts removed
import * as z from "zod";

const ImageSchema = z.object({
  __image_url__: z.string().url().default("https://images.pexels.com/photos/31527637/pexels-photo-31527637.jpeg").meta({
    description: "URL to image",
  }),
  __image_prompt__: z.string().min(10).max(90).default("Professional business meeting scene for roadmap presentation image").meta({
    description: "Prompt used to generate the image. Max 18 words",
  }),
})

const IconSchema = z.object({
  __icon_url__: z.string().default("").meta({
    description: "URL to icon",
  }),
  __icon_query__: z.string().min(3).max(30).default("").meta({
    description: "Query used to search the icon. Max 6 words",
  }),
})

const layoutId = "header-bullets-image-split-slide"
const layoutName = "번호 글머리 단일 이미지"
const layoutDescription = "상단 바, 번호 글머리가 있는 왼쪽 열, 가운데 이미지 패널, 그리고 제목과 단락이 세로로 쌓인 오른쪽 영역으로 구성된 슬라이드"

const Schema = z.object({
  metaMaxWords: z.number().default(18).meta({
    description: "Maximum number of words allowed in any prompt/description metadata fields.",
  }),
  topBar: z.object({

    lineIcon: IconSchema.default({
      __icon_url__: "",
      __icon_query__: "thin green line",
    }).meta({
      description: "Decorative line representation with query only",
    }),
  }).default({

    lineIcon: {
      __icon_url__: "",
      __icon_query__: "thin green line",
    },
  }),
  leftBullets: z.array(
    z.object({
      numberText: z.string().min(2).max(2).default("01").meta({
        description: "Two-digit bullet number. Max 2 chars",
      }),
      title: z.string().min(10).max(36).default("전략 실행").meta({
        description: "Bullet title text. Designed for 24px. Max ~36 chars",
      }),
      body: z.string().min(60).max(100).default("여기에 글머리 내용을 설명하는 예시 문구를 입력하세요.").meta({
        description: "Bullet body text. Max ~100 chars",
      }),
    })
  ).min(1).max(4).default([
    {
      numberText: "01",
      title: "전략 실행",
      body: "여기에 글머리 내용을 설명하는 예시 문구를 입력하세요.",
    },
    {
      numberText: "02",
      title: "강력한 팀 구축",
      body: "여기에 글머리 내용을 설명하는 예시 문구를 입력하세요.",
    },
    {
      numberText: "03",
      title: "시장 확장 전략",
      body: "여기에 글머리 내용을 설명하는 예시 문구를 입력하세요.",
    },
    {
      numberText: "04",
      title: "혁신 파이프라인",
      body: "여기에 글머리 내용을 설명하는 예시 문구를 입력하세요.",
    }
  ]).meta({
    description: "List of numbered bullets. Max 5 items",
  }),
  middleImage: ImageSchema.default({
    __image_url__: "https://images.unsplash.com/photo-1515623959088-7617915baa1e?q=80&w=687&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D",
    __image_prompt__: "Professional business meeting scene for roadmap presentation image",
  }).meta({
    description: "Image displayed in the middle column",
  }),
  rightHeader: z.object({
    heading: z.string().min(6).max(30).default("우리의 여정").meta({
      description: "Right column heading. Max ~30 chars",
    }),
    paragraph: z.string().min(80).max(200).default("여기에 제목을 뒷받침하는 설명을 입력하는 예시 문구입니다. 메시지를 명확하게 전달할 수 있도록 간결하게 작성하세요.").meta({
      description: "Right paragraph text. Max 200 chars",
    }),
  }).default({
    heading: "우리의 여정",
    paragraph: "여기에 제목을 뒷받침하는 설명을 입력하는 예시 문구입니다. 메시지를 명확하게 전달할 수 있도록 간결하게 작성하세요.",
  }),
})

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const dynamicSlideLayout: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
  const bullets = slideData?.leftBullets || []

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />
      <div className=" w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden" style={{ fontFamily: "var(--heading-font-family,Playfair Display)", backgroundColor: 'var(--background-color, #FFFFFF)' }}>
        <div className="flex items-center justify-between px-10 pt-6">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1">

              {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
              {(slideData as any)?.__companyName__ && <span className="text-[18px]  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>{(slideData as any)?.__companyName__ || "Pitchdeck"}</span>}
            </div>
            <svg className="shrink-0" width="220" height="2" viewBox="0 0 220 2" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M0 1H220" stroke="var(--background-text, #111827)" strokeWidth="2" />
            </svg>
          </div>
          {/* page number removed */}
        </div>

        <div className="grid grid-cols-[34%_33%_33%] h-[calc(100%-64px)] mt-1">
          <div className="pl-10 pr-5 pt-8">
            <ul className="flex flex-col gap-5">
              {bullets.map((b, i) => (
                <li key={i} className="flex items-center justify-between">
                  <div className="w-[85%]">
                    <h3 className="text-[24px] leading-tight  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                      {b.title}
                    </h3>
                    <p className="mt-2 text-[16px] leading-relaxed " style={{ color: 'var(--background-text, #6B7280)' }}>
                      {b.body}
                    </p>
                  </div>
                  <div className="w-12 h-12 rounded-full text-white flex items-center justify-center text-[16px]  shadow-[0_12px_30px_rgba(0,0,0,0.12)]" style={{ backgroundColor: 'var(--primary-color, #1B8C2D)', color: 'var(--primary-text, #FFFFFF)' }}>
                    {b.numberText}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="relative" style={{ backgroundColor: 'var(--background-text, #E5E7EB)' }}>
            <img
              src={slideData?.middleImage?.__image_url__ || ""}
              alt={slideData?.middleImage?.__image_prompt__ || "image"}
              className="absolute inset-0 w-full h-full object-cover"
            />
          </div>

          <div className="pl-10 pr-12 pt-16">
            <div className="max-w-[560px] mx-auto">
              <h1 className="text-[64px] leading-[0.95]  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                <span className="block">{slideData?.rightHeader?.heading || "우리의 여정"}</span>
              </h1>
              <p className="mt-6 text-[16px] leading-relaxed " style={{ color: 'var(--background-text, #6B7280)' }}>
                {slideData?.rightHeader?.paragraph || "여기에 제목을 뒷받침하는 설명을 입력하는 예시 문구입니다. 메시지를 명확하게 전달할 수 있도록 간결하게 작성하세요."}
              </p>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

export { Schema, layoutId, layoutName, layoutDescription }
export default dynamicSlideLayout