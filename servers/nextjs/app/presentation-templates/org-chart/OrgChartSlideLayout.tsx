import React from 'react'
import * as z from "zod";

export const layoutId = 'org-chart-slide'
export const layoutName = '조직도'
export const layoutDescription = '최상위 직책과 그 아래 부문/팀 목록으로 구성된 한국형 조직도 슬라이드. 회사 소개, 거버넌스, 조직 구조 설명에 사용하세요.'

const DEFAULT_TOP = { name: '대표이사', role: 'CEO' }

const DEFAULT_DEPARTMENTS = [
  { name: '경영지원본부', lead: '재무·인사·총무', members: ['재무팀', '인사팀', '총무팀'] },
  { name: '사업본부', lead: '영업·마케팅', members: ['국내영업팀', '해외영업팀', '마케팅팀'] },
  { name: '기술연구소', lead: '제품·R&D', members: ['플랫폼팀', '데이터팀', 'QA팀'] },
]

export const Schema = z.object({
  title: z
    .string()
    .min(3)
    .max(40)
    .default('조직 구조')
    .meta({ description: "Slide title for the organization chart" }),
  top: z
    .object({
      name: z.string().min(1).max(20).default('대표이사').meta({ description: "Top-level role or person (e.g. CEO, 대표이사)" }),
      role: z.string().min(1).max(20).default('CEO').meta({ description: "Short caption under the top box" }),
    })
    .default(DEFAULT_TOP)
    .meta({ description: "The single top node of the organization" }),
  departments: z
    .array(
      z.object({
        name: z.string().min(1).max(20).default('부문').meta({ description: "Department or division name" }),
        lead: z.string().min(1).max(24).default('담당 영역').meta({ description: "Short caption: the department's scope or lead" }),
        members: z
          .array(z.string().min(1).max(16))
          .min(0)
          .max(4)
          .default([])
          .meta({ description: "Teams or members under the department (0 to 4)" }),
      })
    )
    .min(2)
    .max(5)
    .default(DEFAULT_DEPARTMENTS)
    .meta({ description: "Departments reporting to the top node, 2 to 5" }),
})

export type OrgChartData = z.infer<typeof Schema>

const OrgChartSlideLayout: React.FC<{ data?: Partial<OrgChartData> }> = ({ data: slideData }) => {
  const title = slideData?.title || '조직 구조'
  const top = slideData?.top || DEFAULT_TOP
  const departments = slideData?.departments && slideData.departments.length > 0 ? slideData.departments : DEFAULT_DEPARTMENTS
  const count = departments.length
  const inset = 50 / count
  const cols = { gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
      >
        <div className="flex flex-col h-full w-full px-20 py-14">
          {/* Title */}
          <div className="flex items-center gap-4 mb-10">
            <div className="w-2 h-12 rounded-full" style={{ background: "var(--primary-color,#2563eb)" }} />
            <h1 className="text-4xl font-black tracking-tight" style={{ color: "var(--background-text,#1a1a2e)" }}>
              {title}
            </h1>
          </div>

          {/* Org chart */}
          <div className="flex-1 flex flex-col items-center justify-center min-h-0">
            {/* Top node */}
            <div className="rounded-2xl px-12 py-5 text-center" style={{ background: "var(--primary-color,#2563eb)" }}>
              <div className="text-xl font-black" style={{ color: "var(--primary-text,#ffffff)" }}>
                {top?.name || '대표이사'}
              </div>
              <div className="text-sm font-medium opacity-80" style={{ color: "var(--primary-text,#ffffff)" }}>
                {top?.role || 'CEO'}
              </div>
            </div>

            {/* Vertical connector */}
            <div className="w-0.5 h-7" style={{ background: "var(--stroke,#e5e7eb)" }} />

            {/* Bus + departments */}
            <div className="w-full relative">
              {/* Horizontal bus connecting all departments */}
              <div className="absolute top-0 h-0.5" style={{ left: `${inset}%`, right: `${inset}%`, background: "var(--stroke,#e5e7eb)" }} />
              <div className="grid" style={cols}>
                {departments.map((d, i) => (
                  <div key={i} className="flex flex-col items-center px-3">
                    {/* drop line from the bus to the card */}
                    <div className="w-0.5 h-7" style={{ background: "var(--stroke,#e5e7eb)" }} />
                    {/* department card */}
                    <div
                      className="w-full rounded-2xl border px-5 py-5 text-center"
                      style={{ backgroundColor: "var(--card-color,#ffffff)", borderColor: "var(--stroke,#e5e7eb)" }}
                    >
                      <div className="text-lg font-bold mb-1" style={{ color: "var(--background-text,#1a1a2e)" }}>
                        {d?.name || '부문'}
                      </div>
                      <div className="text-xs font-medium opacity-55 mb-4" style={{ color: "var(--background-text,#1a1a2e)" }}>
                        {d?.lead || ''}
                      </div>
                      {d?.members && d.members.length > 0 && (
                        <div className="flex flex-col gap-2">
                          {d.members.map((mem, mi) => (
                            <div
                              key={mi}
                              className="rounded-lg py-2 text-sm font-medium"
                              style={{ background: "color-mix(in srgb, var(--primary-color,#2563eb) 8%, transparent)", color: "var(--background-text,#1a1a2e)" }}
                            >
                              {mem}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

export default OrgChartSlideLayout
