import React from "react"
import * as z from "zod"

const layoutId = "IntroSlideLayout"
const layoutName = "소개 슬라이드 레이아웃"
const layoutDescription = "헤더, 제목, 부제목, 본문, 이미지가 있는 소개 슬라이드입니다. 마지막 슬라이드로 사용하는 경우 소개 카드를 비활성화해야 합니다."

const ImageSchema = z
  .object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1522199710521-72d69614c702?w=1200&q=80&auto=format&fit=crop"),
    __image_prompt__: z.string().min(0).max(120).default("abstract gradient background"),
  })
  .default({
    __image_url__:
      "https://images.unsplash.com/photo-1522199710521-72d69614c702?w=1200&q=80&auto=format&fit=crop",
    __image_prompt__: "abstract gradient background",
  })

const Schema = z
  .object({

    title: z
      .string()
      .min(12)
      .max(68)
      .default("피치 덱")
      .meta({ description: "Main slide title" }),

    subtitlePrefix: z.string().min(3).max(40).default("프레젠테이션"),
    subtitleAccent: z.string().min(3).max(40).default("템플릿"),

    paragraph: z
      .string()
      .min(40)
      .max(200)
      .default(
        "여기에 프레젠테이션을 소개하는 본문을 입력하세요. 핵심 메시지를 간결하고 명확하게 전달합니다."
      ),

    website: z
      .string()
      .min(6)
      .max(60)
      .default("www.yourwebsite.com"),

    introCard: z
      .object({
        enabled: z.boolean().default(false),
        name: z.string().min(3).max(40).default("홍길동"),
        date: z.string().min(4).max(40).default("2025년 1월 1일"),
      })
      .default({ enabled: true, name: "홍길동", date: "2025년 1월 1일" }),

    media: z
      .object({
        type: z.literal("image").default("image"),
        image: ImageSchema,
      })
      .default({ type: "image", image: ImageSchema.parse({}) }),
  })
  .default({
    title: "피치 덱",
    subtitlePrefix: "프레젠테이션",
    subtitleAccent: "템플릿",
    paragraph:
      "여기에 프레젠테이션을 소개하는 본문을 입력하세요. 핵심 메시지를 간결하고 명확하게 전달합니다.",
    website: "www.yourwebsite.com",
    introCard: { enabled: true, name: "홍길동", date: "2025년 1월 1일" },
    media: { type: "image", image: ImageSchema.parse({}) },
  })

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const IntroSlideLayout: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
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
        {/* Header: diamond + business name */}
        <div className="px-12 pt-6 pb-2">
          <div className="flex items-center gap-3">
            <div
              className="w-3 h-3 rotate-45"
              style={{ backgroundColor: "var(--background-text, #111827)" }}
            ></div>
            <div className="flex items-center gap-1">

              {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
              {(slideData as any)?.__companyName__ && <span className="text-[16px]" style={{ color: "var(--background-text, #6B7280)" }}>{(slideData as any)?.__companyName__}</span>}
            </div>
          </div>
        </div>

        {/* Right panel image (replaces dark gradient box) */}
        <div className="absolute top-0 right-0 h-[520px] w-[36%] overflow-hidden">
          <img
            src={slideData?.media?.image?.__image_url__}
            alt={slideData?.media?.image?.__image_prompt__}
            className="absolute inset-0 w-full h-full object-cover"
          />
        </div>

        {/* Vertical diamond decorations */}
        <div className="absolute top-8 right-[38%] flex flex-col items-center gap-3">
          <div className="w-3 h-3 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
          <div className="w-3 h-3 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
          <div className="w-3 h-3 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
        </div>

        <div className="grid grid-cols-[58%_42%] gap-10 px-12 pt-4 pb-10 items-start">
          <div className="pt-4">
            <h1
              className="text-[64px] leading-[1.05] tracking-tight  font-semibold"
              style={{ color: "var(--background-text, #111827)" }}
            >
              {slideData?.title}
            </h1>
            <p className="mt-5 text-[16px] leading-[1.6] max-w-[620px] " style={{ color: "var(--background-text, #6B7280)" }}>{slideData?.paragraph}</p>

            {slideData?.introCard?.enabled && (
              <div
                className="mt-6 inline-flex items-center gap-4 rounded-sm border px-4 py-3 "
                style={{
                  borderColor: 'var(--primary-color, #BFF4FF)',
                  backgroundColor: 'var(--primary-color, #BFF4FF)'
                }}
              >
                <div
                  className="w-2 h-6"
                  style={{ backgroundColor: 'var(--primary-text, #111827)' }}
                ></div>
                <div className="flex items-baseline gap-3">
                  <span className="text-[16px] font-semibold" style={{ color: 'var(--primary-text, #111827)' }}>
                    {slideData?.introCard?.name}
                  </span>
                  <span className="text-[14px]" style={{ color: 'var(--primary-text, #6B7280)' }}>
                    {slideData?.introCard?.date}
                  </span>
                </div>
              </div>
            )}
          </div>

        </div>

        {/* Footer line with website and end diamond */}
        <div className="absolute bottom-8 left-12 right-12 flex items-center">
          <span className="text-[14px] " style={{ color: "var(--background-text, #6B7280)" }}>{slideData?.website}</span>
          <div className="ml-6 h-[2px] flex-1" style={{ backgroundColor: "var(--background-text, #E5E7EB)" }}></div>
        </div>

        {/* Big bottom-right diamond */}
        <div className="absolute bottom-7 right-6 w-8 h-8 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
      </div>
    </>
  )
}

export { Schema, layoutId, layoutName, layoutDescription }
export default IntroSlideLayout


