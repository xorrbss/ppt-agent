import React from 'react'
import * as z from "zod";
import EditableText from '@/app/(presentation-generator)/components/EditableText';

export const layoutId = 'comparison-table-slide'
export const layoutName = '데이터 비교표'
export const layoutDescription = '제목과, 헤더 행 + 항목별 데이터 행으로 구성된 한국형 비교/요약 표 슬라이드. 연도·항목·경쟁사 비교 등 정형 데이터에 사용하세요.'

const DEFAULT_COLUMNS = ['구분', '2023', '2024', '2025']

const DEFAULT_ROWS = [
  { label: '매출액', values: ['₩410억', '₩480억', '₩560억'], highlight: false },
  { label: '영업이익', values: ['₩52억', '₩71억', '₩103억'], highlight: false },
  { label: '영업이익률', values: ['12.7%', '14.8%', '18.4%'], highlight: true },
  { label: '고객사 수', values: ['142', '189', '247'], highlight: false },
]

export const Schema = z.object({
  title: z
    .string()
    .min(3)
    .max(40)
    .default('주요 재무 지표 비교')
    .meta({ description: "Slide title summarizing the comparison table" }),
  columns: z
    .array(z.string().min(1).max(16))
    .min(2)
    .max(5)
    .default(DEFAULT_COLUMNS)
    .meta({ description: "Header cells. The first is the row-label column header; the rest are the compared columns (years, items, competitors)." }),
  rows: z
    .array(
      z.object({
        label: z
          .string()
          .min(1)
          .max(24)
          .default('항목')
          .meta({ description: "Row header shown in the first column" }),
        values: z
          .array(z.string().min(1).max(16))
          .min(1)
          .max(4)
          .default(['-', '-', '-'])
          .meta({ description: "Cell values for each compared column, in the same order as the header (excluding the first label column)" }),
        highlight: z
          .boolean()
          .default(false)
          .meta({ description: "Whether to emphasize this row (e.g. the single most important metric)" }),
      })
    )
    .min(2)
    .max(6)
    .default(DEFAULT_ROWS)
    .meta({ description: "Data rows, each with a label and one value per compared column" }),
  note: z
    .string()
    .min(2)
    .max(80)
    .default('단위: 원 / 연결 재무제표 기준')
    .meta({ description: "Optional footnote such as units or data source" }),
})

export type ComparisonTableData = z.infer<typeof Schema>

const ComparisonTableSlideLayout: React.FC<{ data?: Partial<ComparisonTableData> }> = ({ data: slideData }) => {
  const title = slideData?.title || '주요 재무 지표 비교'
  const columns = slideData?.columns && slideData.columns.length > 0 ? slideData.columns : DEFAULT_COLUMNS
  const rows = slideData?.rows && slideData.rows.length > 0 ? slideData.rows : DEFAULT_ROWS
  const note = slideData?.note || ''
  const dataColCount = Math.max(1, columns.length - 1)

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
      >
        <div className="flex flex-col h-full w-full px-20 py-16">
          {/* Title */}
          <div className="flex items-center gap-4 mb-10">
            <div className="w-2 h-12 rounded-full" style={{ background: "var(--primary-color,#2563eb)" }} />
            <EditableText
              as="h1"
              path="title"
              value={title}
              className="text-4xl font-black tracking-tight"
              style={{ color: "var(--background-text,#1a1a2e)" }}
            />
          </div>

          {/* Table */}
          <div
            className="flex-1 min-h-0 rounded-2xl border overflow-hidden"
            style={{ borderColor: "var(--stroke,#e5e7eb)" }}
          >
            <table className="w-full border-collapse">
              <thead>
                <tr style={{ background: "var(--primary-color,#2563eb)" }}>
                  {columns.map((col, i) => (
                    <EditableText
                      key={i}
                      as="th"
                      path={`columns[${i}]`}
                      value={col}
                      className={`px-6 py-4 text-sm font-bold tracking-wide ${i === 0 ? 'text-left' : 'text-right'}`}
                      style={{ color: "var(--primary-text,#ffffff)", width: i === 0 ? '28%' : `${72 / dataColCount}%` }}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => (
                  <tr
                    key={ri}
                    style={{
                      borderTop: "1px solid var(--stroke,#e5e7eb)",
                      backgroundColor: row?.highlight
                        ? "color-mix(in srgb, var(--primary-color,#2563eb) 8%, transparent)"
                        : "var(--card-color,#ffffff)",
                    }}
                  >
                    <EditableText
                      as="td"
                      path={`rows[${ri}].label`}
                      value={row?.label || '항목'}
                      className="px-6 py-4 text-left text-base font-bold"
                      style={{ color: "var(--background-text,#1a1a2e)" }}
                    />
                    {Array.from({ length: dataColCount }).map((_, ci) => (
                      <EditableText
                        key={ci}
                        as="td"
                        path={`rows[${ri}].values[${ci}]`}
                        value={row?.values?.[ci] ?? '-'}
                        className="px-6 py-4 text-right text-base"
                        style={{ color: "var(--background-text,#1a1a2e)", opacity: row?.highlight ? 1 : 0.82, fontWeight: row?.highlight ? 700 : 500 }}
                      />
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Note */}
          {note && (
            <EditableText
              as="p"
              path="note"
              value={note}
              className="mt-5 text-sm opacity-50"
              style={{ color: "var(--background-text,#1a1a2e)" }}
            />
          )}
        </div>
      </div>
    </>
  )
}

export default ComparisonTableSlideLayout
