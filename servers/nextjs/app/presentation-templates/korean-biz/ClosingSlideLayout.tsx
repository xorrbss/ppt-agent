import React from 'react'
import * as z from "zod";
import EditableText from '@/app/(presentation-generator)/components/EditableText';

export const layoutId = 'korean-biz-closing'
export const layoutName = '마무리'
export const layoutDescription = '메시지와 연락처 정보를 담은 한국형 마무리 / 감사 슬라이드'

export const Schema = z.object({
  title: z
    .string()
    .min(2)
    .max(30)
    .default('감사합니다')
    .meta({ description: "Large centered closing title, e.g. a thank-you phrase" }),
  message: z
    .string()
    .min(5)
    .max(120)
    .default('여러분의 관심에 진심으로 감사드립니다.')
    .meta({ description: "Short closing message shown under the title" }),
  contactEmail: z
    .string()
    .min(3)
    .max(60)
    .default('contact@company.co.kr')
    .meta({ description: "Contact email address shown in the footer" }),
  contactName: z
    .string()
    .min(2)
    .max(40)
    .default('김민준 | 전략기획팀')
    .meta({ description: "Contact person name and team/role shown in the footer" }),
})

export type ClosingSlideData = z.infer<typeof Schema>

const ClosingSlideLayout: React.FC<{ data?: Partial<ClosingSlideData> }> = ({ data: slideData }) => (
  <>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
    <div
      className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
      style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
    >
      <div className="w-full h-full flex flex-col px-24 py-16">
        {/* Centered main content */}
        <div className="flex-1 flex flex-col items-center justify-center text-center">
          <div
            className="w-20 h-1.5 rounded-full mb-10"
            style={{ background: "var(--primary-color,#2563eb)" }}
          />
          <EditableText
            as="h1"
            path="title"
            value={slideData?.title || '감사합니다'}
            className="font-black tracking-tight leading-none"
            style={{ color: "var(--background-text,#1a1a2e)", fontSize: "84px" }}
          />
          <EditableText
            as="p"
            path="message"
            value={slideData?.message || '여러분의 관심에 진심으로 감사드립니다.'}
            className="mt-8 text-2xl font-medium max-w-3xl leading-relaxed"
            style={{ color: "var(--background-text,#1a1a2e)", opacity: 0.78 }}
          />
        </div>

        {/* Footer contact row */}
        <div
          className="flex items-center justify-between pt-8 mt-4 border-t"
          style={{ borderColor: "var(--stroke,#e5e7eb)" }}
        >
          <div className="flex items-center gap-4">
            <div
              className="w-1 h-10 rounded-full"
              style={{ background: "var(--primary-color,#2563eb)" }}
            />
            <EditableText
              as="span"
              path="contactName"
              value={slideData?.contactName || '김민준 | 전략기획팀'}
              className="text-lg font-bold"
              style={{ color: "var(--background-text,#1a1a2e)" }}
            />
          </div>
          <EditableText
            as="span"
            path="contactEmail"
            value={slideData?.contactEmail || 'contact@company.co.kr'}
            className="text-lg font-medium tracking-wide"
            style={{ color: "var(--primary-color,#2563eb)" }}
          />
        </div>
      </div>
    </div>
  </>
)

export default ClosingSlideLayout
