import React from 'react'
import * as z from "zod";
import { ImageSchema } from '../defaultSchemes';

export const layoutId = 'korean-biz-cover'
export const layoutName = '표지'
export const layoutDescription = '제목, 부제목, 발표자, 일자, 보조 이미지를 포함한 한국형 비즈니스 표지 슬라이드'

export const Schema = z.object({
  title: z
    .string()
    .min(3)
    .max(40)
    .default('2025 사업 전략 발표')
    .meta({ description: "Main cover title of the presentation" }),
  subtitle: z
    .string()
    .min(3)
    .max(80)
    .default('지속 가능한 성장을 위한 핵심 과제')
    .meta({ description: "Supporting subtitle shown under the title" }),
  presenterName: z
    .string()
    .min(2)
    .max(40)
    .default('김민준')
    .meta({ description: "Name of the presenter or presenting team" }),
  presentationDate: z
    .string()
    .min(2)
    .max(40)
    .default('2025년 6월')
    .meta({ description: "Date or period of the presentation" }),
  image: ImageSchema.default({
    __image_url__: 'https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=1200&q=80',
    __image_prompt__: 'modern office',
  }).meta({ description: "Supporting cover image shown as a side panel" }),
})

export type CoverSlideData = z.infer<typeof Schema>

const CoverSlideLayout: React.FC<{ data?: Partial<CoverSlideData> }> = ({ data: slideData }) => (
  <>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
    <div
      className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
      style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
    >
      <div className="flex h-full w-full">
        {/* Left content panel */}
        <div className="flex flex-col justify-between flex-1 pl-20 pr-12 py-16">
          <div className="flex flex-col gap-8">
            {/* Accent eyebrow */}
            <div className="flex items-center gap-3">
              <div
                className="h-1.5 w-12 rounded-full"
                style={{ background: "var(--primary-color,#2563eb)" }}
              />
              <span
                className="text-sm font-medium tracking-widest uppercase"
                style={{ color: "var(--primary-color,#2563eb)" }}
              >
                프레젠테이션
              </span>
            </div>

            {/* Title */}
            <h1
              className="text-6xl font-black leading-tight tracking-tight"
              style={{ color: "var(--background-text,#1a1a2e)" }}
            >
              {slideData?.title || '2025 사업 전략 발표'}
            </h1>

            {/* Subtitle with accent underline */}
            <div className="flex flex-col gap-4">
              <p
                className="text-2xl font-medium leading-snug max-w-xl"
                style={{ color: "var(--background-text,#1a1a2e)", opacity: 0.78 }}
              >
                {slideData?.subtitle || '지속 가능한 성장을 위한 핵심 과제'}
              </p>
              <div
                className="h-1.5 w-24 rounded-full"
                style={{ background: "var(--primary-color,#2563eb)" }}
              />
            </div>
          </div>

          {/* Presenter + date card */}
          <div
            className="inline-flex items-center gap-8 self-start rounded-xl border px-7 py-5"
            style={{ backgroundColor: "var(--card-color,#ffffff)", borderColor: "var(--stroke,#e5e7eb)" }}
          >
            <div className="flex flex-col gap-1">
              <span
                className="text-xs font-medium tracking-wide uppercase"
                style={{ color: "var(--background-text,#1a1a2e)", opacity: 0.55 }}
              >
                발표자
              </span>
              <span
                className="text-lg font-bold"
                style={{ color: "var(--background-text,#1a1a2e)" }}
              >
                {slideData?.presenterName || '김민준'}
              </span>
            </div>
            <div
              className="h-10 w-px"
              style={{ background: "var(--stroke,#e5e7eb)" }}
            />
            <div className="flex flex-col gap-1">
              <span
                className="text-xs font-medium tracking-wide uppercase"
                style={{ color: "var(--background-text,#1a1a2e)", opacity: 0.55 }}
              >
                일자
              </span>
              <span
                className="text-lg font-bold"
                style={{ color: "var(--background-text,#1a1a2e)" }}
              >
                {slideData?.presentationDate || '2025년 6월'}
              </span>
            </div>
          </div>
        </div>

        {/* Right image panel */}
        <div className="relative w-[42%] h-full">
          <div
            className="absolute inset-0"
            style={{ background: "var(--primary-color,#2563eb)" }}
          />
          <img
            src={slideData?.image?.__image_url__ || 'https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=1200&q=80'}
            alt={slideData?.image?.__image_prompt__ || 'modern office'}
            className="absolute inset-0 h-full w-full object-cover mix-blend-luminosity opacity-90"
          />
          <div
            className="absolute inset-0"
            style={{ background: "linear-gradient(135deg, var(--primary-color,#2563eb) 0%, transparent 55%)", opacity: 0.45 }}
          />
        </div>
      </div>
    </div>
  </>
)

export default CoverSlideLayout
