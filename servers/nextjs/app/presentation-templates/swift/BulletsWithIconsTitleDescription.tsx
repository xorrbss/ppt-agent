import React from "react"
import * as z from "zod"

import { IconSchema } from '../defaultSchemes';
import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";

const layoutId = "bullet-with-icons-title-description"
const layoutName = "아이콘·제목·설명 글머리"
const layoutDescription = "아이콘이 있는 글머리에 각 항목의 제목·설명과 전체 제목·설명을 함께 표시"

const ItemSchema = z
  .object({
    icon: IconSchema,
    title: z.string().min(3).max(40).default("예시 제목"),
    description: z
      .string()
      .min(0)
      .max(160)
      .default(
        "아이콘 제목 아래에 들어가는 간단한 보조 설명입니다."
      ),
  })
  .default({
    icon: {
      __icon_url__:
        "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/graduation-cap-bold.svg",
      __icon_query__: "feature icon",
    },
    title: "예시 제목",
    description: "아이콘 제목 아래에 들어가는 간단한 보조 설명입니다.",
  })

const Schema = z
  .object({
    title: z
      .string()
      .min(3)
      .max(60)
      .default("우리의 인포그래픽"),
    sideHeading: z.string().min(0).max(60).default("예시 부제목 문구입니다,"),
    sideParagraph: z
      .string()
      .min(0)
      .max(300)
      .default(
        "맥락을 설명하는 간결한 문단입니다. 한두 줄로 짧고 읽기 쉽게 작성하세요."
      ),
    items: z
      .array(ItemSchema)
      .min(3)
      .max(4)
      .default([
        {
          icon: {
            __icon_url__:
              "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/graduation-cap-bold.svg",
            __icon_query__: "feature icon",
          },
          title: "예시 제목",
          description:
            "첫 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
        },
        {
          icon: {
            __icon_url__:
              "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/user-bold.svg",
            __icon_query__: "feature icon",
          },
          title: "예시 제목",
          description:
            "두 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
        },
        {
          icon: {
            __icon_url__:
              "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/calendar-blank-bold.svg",
            __icon_query__: "feature icon",
          },
          title: "예시 제목",
          description:
            "세 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
        },
        {
          icon: {
            __icon_url__:
              "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/x-bold.svg",
            __icon_query__: "feature icon",
          },
          title: "예시 제목",
          description:
            "네 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
        },
      ]),
    website: z.string().min(6).max(60).default("www.yourwebsite.com"),
  })
  .default({
    title: "우리의 인포그래픽",
    sideHeading: "예시 부제목 문구입니다,",
    sideParagraph:
      "맥락을 설명하는 간결한 문단입니다. 한두 줄로 짧고 읽기 쉽게 작성하세요.",
    items: [
      {
        icon: { __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/graduation-cap-bold.svg", __icon_query__: "feature icon" },
        title: "예시 제목",
        description:
          "첫 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
      },
      {
        icon: { __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/user-bold.svg", __icon_query__: "feature icon" },
        title: "예시 제목",
        description:
          "두 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
      },
      {
        icon: { __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/calendar-blank-bold.svg", __icon_query__: "feature icon" },
        title: "예시 제목",
        description:
          "세 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
      },
      {
        icon: { __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/x-bold.svg", __icon_query__: "feature icon" },
        title: "예시 제목",
        description:
          "네 번째 아이콘 아래에서 요점을 설명하는 간결한 보조 텍스트입니다.",
      },
    ],
    website: "www.yourwebsite.com",
  })

type SlideData = z.infer<typeof Schema>

interface SlideLayoutProps {
  data?: Partial<SlideData>
}

const InfographicFourIcons: React.FC<SlideLayoutProps> = ({ data }) => {
  const slideData = data || {}
  const items = slideData.items || []

  const renderTitle = (title?: string) => {
    if (!title) return null
    const parts = title.split("\n")
    return (
      <>
        {parts.map((p, i) => (
          <div key={i}>{p}</div>
        ))}
      </>
    )
  }

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
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-3 h-3 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
              <div className="flex items-center gap-1">

                {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
                {(slideData as any)?.__companyName__ && <span className="text-[16px]" style={{ color: "var(--background-text, #6B7280)" }}>{(slideData as any)?.__companyName__}</span>}
              </div>
            </div>
          </div>
        </div>

        {/* Title + right paragraph */}
        <div className="px-12 pt-2">
          <div className="grid grid-cols-[1.2fr_1fr] gap-10 items-start">
            <div className="text-[56px] leading-[1.05] font-semibold" style={{ color: "var(--background-text, #111827)" }}>
              {renderTitle(slideData.title)}
            </div>
            <div>
              {slideData.sideHeading && (
                <div className="text-[16px] mb-2 font-semibold" style={{ color: "var(--background-text, #111827)" }}>
                  {slideData.sideHeading}
                </div>
              )}
              {slideData.sideParagraph && (
                <div className="text-[14px] leading-[1.6]" style={{ color: "var(--background-text, #6B7280)" }}>
                  {slideData.sideParagraph}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Icons row */}
        <div className="px-12 pt-12">
          <div className="grid grid-flow-col auto-cols-[260px] gap-8 justify-center">
            {items.slice(0, 4).map((item, idx) => (
              <div key={idx} className="flex flex-col items-center text-center">
                <div className="relative">
                  <div
                    className="w-40 h-40 rounded-full flex items-center justify-center shadow"
                    style={{ backgroundColor: 'var(--primary-color, #BFF4FF)' }}
                  >
                    {/* Icon */}
                    <RemoteSvgIcon
                      url={item.icon.__icon_url__}
                      strokeColor={"currentColor"}
                      className="w-14 h-14"
                      color="var(--primary-text, #111827)"
                      title={item.icon.__icon_query__}
                    />
                  </div>
                </div>
                <div className="mt-5 text-[16px] font-semibold" style={{ color: "var(--background-text, #111827)" }}>
                  {item.title}
                </div>
                <div className="mt-2 text-[13px] leading-[1.6] max-w-[260px]" style={{ color: "var(--background-text, #6B7280)" }}>
                  {item.description}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="absolute bottom-8 left-12 right-12 flex items-center">
          <span className="text-[14px]" style={{ color: "var(--background-text, #6B7280)" }}>{slideData.website}</span>
          <div className="ml-6 h-[2px] flex-1" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
        </div>
        <div className="absolute bottom-7 right-6 w-8 h-8 rotate-45" style={{ backgroundColor: "var(--background-text, #111827)" }}></div>
      </div>
    </>
  )
}

export { Schema, layoutId, layoutName, layoutDescription }
export default InfographicFourIcons


