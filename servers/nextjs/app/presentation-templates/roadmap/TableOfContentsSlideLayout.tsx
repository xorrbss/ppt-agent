import React from 'react'
import * as z from "zod";
import EditableText from '@/app/(presentation-generator)/components/EditableText';

export const layoutId = 'korean-biz-toc'
export const layoutName = '목차'
export const layoutDescription = '섹션 제목을 나열하는 한국형 목차 슬라이드'

export const Schema = z.object({
  title: z
    .string()
    .min(2)
    .max(40)
    .default('목차')
    .meta({ description: "Slide heading shown above the agenda list (e.g. Table of Contents)" }),
  items: z
    .array(
      z.object({
        title: z
          .string()
          .min(2)
          .max(40)
          .default('항목')
          .meta({ description: "Section/agenda item title for one row of the table of contents" }),
      })
    )
    .min(3)
    .max(6)
    .default([
      { title: '시장 현황' },
      { title: '핵심 전략' },
      { title: '실행 계획' },
      { title: '기대 효과' },
    ])
    .meta({ description: "Ordered list of agenda section titles, numbered 01, 02, ... in the layout" }),
})

export type TableOfContentsData = z.infer<typeof Schema>

const TableOfContentsSlideLayout: React.FC<{ data?: Partial<TableOfContentsData> }> = ({ data: slideData }) => {
  const items = slideData?.items && slideData.items.length > 0
    ? slideData.items
    : [
        { title: '시장 현황' },
        { title: '핵심 전략' },
        { title: '실행 계획' },
        { title: '기대 효과' },
      ]

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
      >
        <div className="w-full h-full flex flex-col px-20 py-16">
          {/* Header */}
          <div className="mb-12 shrink-0">
            <div
              className="w-16 h-1.5 rounded-full mb-6"
              style={{ background: "var(--primary-color,#2563eb)" }}
            />
            <EditableText
              as="h1"
              path="title"
              value={slideData?.title || '목차'}
              className="text-5xl font-black tracking-tight"
              style={{ color: "var(--background-text,#1a1a2e)" }}
            />
          </div>

          {/* Agenda grid */}
          <div className="flex-1 grid grid-cols-2 gap-x-16 gap-y-8 content-center">
            {items.map((item, index) => (
              <div key={index} className="flex items-center gap-6">
                <div
                  className="shrink-0 w-16 h-16 rounded-xl flex items-center justify-center text-2xl font-black"
                  style={{
                    background: "var(--primary-color,#2563eb)",
                    color: "var(--primary-text,#ffffff)",
                  }}
                >
                  {String(index + 1).padStart(2, '0')}
                </div>
                <div className="flex-1 min-w-0">
                  <EditableText
                    as="p"
                    path={`items[${index}].title`}
                    value={item?.title || '항목'}
                    className="text-2xl font-bold truncate"
                    style={{ color: "var(--background-text,#1a1a2e)" }}
                  />
                  <div
                    className="mt-3 h-px w-full"
                    style={{ background: "var(--stroke,#e5e7eb)" }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}

export default TableOfContentsSlideLayout
