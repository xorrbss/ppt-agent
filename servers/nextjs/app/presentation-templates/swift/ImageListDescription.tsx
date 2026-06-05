import React from "react"
import * as z from "zod"

const layoutId = "image-list-description-slide"
const layoutName = "이미지 목록 설명"
const layoutDescription = "부제목과 설명이 있는 이미지 목록으로, 페이지 전체에 대한 하나의 설명을 포함"

const ImageSchema = z
  .object({
    __image_url__: z
      .string()
      .url()
      .default(
        "https://images.unsplash.com/photo-1522199710521-72d69614c702?w=1200&q=80&auto=format&fit=crop"
      ),
    __image_prompt__: z
      .string()
      .min(0)
      .max(120)
      .default("abstract gradient background"),
  })
  .default({
    __image_url__:
      "https://images.unsplash.com/photo-1522199710521-72d69614c702?w=1200&q=80&auto=format&fit=crop",
    __image_prompt__: "abstract gradient background",
  })

const ItemSchema = z
  .object({
    title: z.string().min(2).max(40).default("예시 제목"),
    description: z
      .string()
      .min(10)
      .max(140)
      .default("이미지나 항목에 대한 간단한 설명입니다."),
    image: ImageSchema,
  })
  .default({
    title: "예시 제목",
    description: "이미지나 항목에 대한 간단한 설명입니다.",
    image: ImageSchema.parse({}),
  })

const Schema = z
  .object({
    titleLine1: z.string().min(3).max(24).default("우리 팀을"),
    titleLine2: z.string().min(3).max(24).default("소개합니다"),
    description: z
      .string()
      .min(20)
      .max(200)
      .default(
        "팀과 구성원을 소개하는 간단한 설명 문장입니다."
      ),
    items: z
      .array(ItemSchema)
      .min(3)
      .max(6)
      .default([
        ItemSchema.parse({}),
        ItemSchema.parse({ title: "두 번째 항목", description: "간결한 보조 텍스트입니다.", image: ImageSchema.parse({}) }),
        ItemSchema.parse({ title: "세 번째 항목", description: "간결한 보조 텍스트입니다.", image: ImageSchema.parse({}) }),
      ]),
    website: z.string().min(6).max(60).default("www.yourwebsite.com"),
  })
  .default({

    titleLine1: "우리 팀을",
    titleLine2: "소개합니다",
    description:
      "팀과 구성원을 소개하는 간단한 설명 문장입니다.",
    items: [
      ItemSchema.parse({}),
      ItemSchema.parse({ title: "두 번째 항목", description: "간결한 보조 텍스트입니다.", image: ImageSchema.parse({}) }),
      ItemSchema.parse({ title: "세 번째 항목", description: "간결한 보조 텍스트입니다.", image: ImageSchema.parse({}) }),
    ],
    website: "www.yourwebsite.com",
  })

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const TeamMembers: React.FC<SlideLayoutProps> = ({ data: slideData }) => {
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

        <div className="px-12 pt-6 grid grid-cols-[36%_64%] gap-8 items-start">
          {/* Left text stack */}
          <div>
            <div
              className="text-[56px] leading-[1.05] font-semibold"
              style={{ color: "var(--background-text, #111827)" }}
            >
              {slideData?.titleLine1}
              <br />
              {slideData?.titleLine2}
            </div>
            <p
              className="mt-8 text-[16px] leading-[1.8] max-w-[300px]"
              style={{ color: "var(--background-text, #6B7280)" }}
            >
              {slideData?.description}
            </p>
          </div>

          {/* Right generic image cards */}
          <div className="grid grid-cols-3 gap-10">
            {items.slice(0, 3).map((it, i) => (
              <div key={i} className="flex flex-col items-stretch">
                {/* Photo block uses provided image */}
                <div className="h-[180px] rounded-t-md overflow-hidden">
                  <img src={it.image.__image_url__} alt={it.image.__image_prompt__} className="w-full h-full object-cover" />
                </div>
                {/* Cyan details panel */}
                <div className="relative -mt-[1px] rounded-b-[28px] px-6 pt-6 pb-7" style={{ backgroundColor: 'var(--primary-color, #BFF4FF)' }}>
                  <div className="text-[20px] font-semibold" style={{ color: 'var(--primary-text, #111827)' }}>{it.title}</div>
                  <p className="mt-3 text-[14px] leading-[1.7]" style={{ color: 'var(--primary-text, #6B7280)' }}>{it.description}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer line with website and end diamond */}
        <div className="absolute bottom-8 left-12 right-12 flex items-center">
          <span className="text-[14px]" style={{ color: "var(--background-text, #6B7280)" }}>{slideData?.website}</span>
          <div className="ml-6 h-[2px] flex-1" style={{ backgroundColor: "#E5E7EB" }}></div>
        </div>

        {/* Big bottom-right diamond */}
        <div className="absolute bottom-7 right-6 w-8 h-8 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
      </div>
    </>
  )
}

export { Schema, layoutId, layoutName, layoutDescription }
export default TeamMembers


