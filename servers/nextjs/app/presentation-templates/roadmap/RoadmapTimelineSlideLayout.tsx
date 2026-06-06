import React from 'react'
import * as z from "zod";

export const layoutId = 'roadmap-timeline-slide'
export const layoutName = '로드맵·연혁'
export const layoutDescription = '제목과 시간 순서의 마일스톤(시기·제목·설명)으로 구성된 한국형 로드맵/연혁 타임라인 슬라이드. 사업 계획, 회사 연혁, 추진 일정에 사용하세요.'

const DEFAULT_MILESTONES = [
  { period: '2023', title: '사업 기반 구축', description: '핵심 제품 출시 및 초기 고객사 확보' },
  { period: '2024', title: '시장 확대', description: '신규 채널 진출과 파트너십 체결로 매출 성장' },
  { period: '2025', title: '수익성 강화', description: '운영 효율화를 통한 영업이익률 개선' },
  { period: '2026', title: '글로벌 진출', description: '해외 거점 설립 및 신규 시장 본격 공략' },
]

export const Schema = z.object({
  title: z
    .string()
    .min(3)
    .max(40)
    .default('사업 추진 로드맵')
    .meta({ description: "Slide title summarizing the roadmap or timeline" }),
  milestones: z
    .array(
      z.object({
        period: z
          .string()
          .min(1)
          .max(14)
          .default('2025')
          .meta({ description: "Time marker such as a year, quarter, or phase (e.g. '2025', '1단계')" }),
        title: z
          .string()
          .min(2)
          .max(20)
          .default('마일스톤')
          .meta({ description: "Short milestone heading" }),
        description: z
          .string()
          .min(6)
          .max(60)
          .default('해당 시기의 핵심 목표를 한 문장으로 설명합니다.')
          .meta({ description: "One short sentence describing the milestone" }),
      })
    )
    .min(3)
    .max(5)
    .default(DEFAULT_MILESTONES)
    .meta({ description: "Ordered milestones, 3 to 5, shown left to right along the timeline" }),
})

export type RoadmapTimelineData = z.infer<typeof Schema>

const RoadmapTimelineSlideLayout: React.FC<{ data?: Partial<RoadmapTimelineData> }> = ({ data: slideData }) => {
  const title = slideData?.title || '사업 추진 로드맵'
  const milestones = slideData?.milestones && slideData.milestones.length > 0 ? slideData.milestones : DEFAULT_MILESTONES
  const count = milestones.length
  const inset = 50 / count
  const cols = { gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
      >
        <div className="flex flex-col h-full w-full px-20 py-16">
          {/* Title */}
          <div className="flex items-center gap-4 mb-16">
            <div className="w-2 h-12 rounded-full" style={{ background: "var(--primary-color,#2563eb)" }} />
            <h1 className="text-4xl font-black tracking-tight" style={{ color: "var(--background-text,#1a1a2e)" }}>
              {title}
            </h1>
          </div>

          {/* Timeline */}
          <div className="flex-1 flex flex-col justify-center">
            {/* Periods */}
            <div className="grid" style={cols}>
              {milestones.map((m, i) => (
                <div key={i} className="text-center text-xl font-black tracking-tight" style={{ color: "var(--primary-color,#2563eb)" }}>
                  {m?.period || '2025'}
                </div>
              ))}
            </div>

            {/* Rail + nodes */}
            <div className="relative my-6">
              <div
                className="absolute top-1/2 -translate-y-1/2 h-1 rounded-full"
                style={{ left: `${inset}%`, right: `${inset}%`, background: "color-mix(in srgb, var(--primary-color,#2563eb) 25%, transparent)" }}
              />
              <div className="relative grid" style={cols}>
                {milestones.map((_, i) => (
                  <div key={i} className="flex justify-center">
                    <div
                      className="w-6 h-6 rounded-full"
                      style={{ background: "var(--primary-color,#2563eb)", boxShadow: "0 0 0 4px var(--background-color,#ffffff), 0 0 0 6px var(--stroke,#e5e7eb)" }}
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* Cards */}
            <div className="grid items-start" style={cols}>
              {milestones.map((m, i) => (
                <div key={i} className="px-4 text-center">
                  <h3 className="text-lg font-bold mb-2" style={{ color: "var(--background-text,#1a1a2e)" }}>
                    {m?.title || '마일스톤'}
                  </h3>
                  <p className="text-sm leading-relaxed opacity-70" style={{ color: "var(--background-text,#1a1a2e)" }}>
                    {m?.description || '설명'}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

export default RoadmapTimelineSlideLayout
