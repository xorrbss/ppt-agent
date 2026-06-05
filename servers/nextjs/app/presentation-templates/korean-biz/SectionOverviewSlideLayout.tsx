import React from 'react'
import * as z from "zod";
import { ImageSchema } from '../defaultSchemes';

export const layoutId = 'korean-biz-overview'
export const layoutName = '개요'
export const layoutDescription = '제목, 긴 설명 문단, 보조 이미지로 구성된 한국형 섹션 개요 슬라이드'

export const Schema = z.object({
    title: z
        .string()
        .min(3)
        .max(40)
        .default('시장 현황')
        .meta({ description: "Section title shown at the top of the text column" }),
    description: z
        .string()
        .min(20)
        .max(220)
        .default('국내 시장은 디지털 전환 가속화로 빠르게 성장하고 있습니다. 주요 산업 전반에서 자동화 수요가 확대되며 신규 사업 기회가 늘어나고 있습니다. 변화하는 고객 요구에 맞춰 시장 구조 또한 빠르게 재편되는 추세입니다.')
        .meta({ description: "A 2-3 sentence paragraph describing the section context or market overview" }),
    image: ImageSchema.default({
        __image_url__: 'https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=1200&q=80',
        __image_prompt__: 'modern office',
    }).meta({ description: "Supporting image displayed in the right column" }),
})

export type SectionOverviewData = z.infer<typeof Schema>

const SectionOverviewSlideLayout: React.FC<{ data?: Partial<SectionOverviewData> }> = ({ data: slideData }) => (
    <>
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />
        <div
            className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
            style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
        >
            <div className="w-full h-full flex">
                {/* Text column */}
                <div className="w-1/2 h-full flex flex-col justify-center px-16 py-20">
                    <h1
                        className="text-5xl font-black leading-tight tracking-tight"
                        style={{ color: "var(--background-text,#1a1a2e)" }}
                    >
                        {slideData?.title || '시장 현황'}
                    </h1>
                    <div
                        className="mt-6 mb-8 h-1.5 w-20 rounded-full"
                        style={{ background: "var(--primary-color,#2563eb)" }}
                    />
                    <p
                        className="text-lg leading-relaxed font-normal"
                        style={{ color: "var(--background-text,#1a1a2e)" }}
                    >
                        {slideData?.description || '국내 시장은 디지털 전환 가속화로 빠르게 성장하고 있습니다. 주요 산업 전반에서 자동화 수요가 확대되며 신규 사업 기회가 늘어나고 있습니다. 변화하는 고객 요구에 맞춰 시장 구조 또한 빠르게 재편되는 추세입니다.'}
                    </p>
                </div>

                {/* Image column */}
                <div className="w-1/2 h-full flex items-center justify-center p-12">
                    <div
                        className="w-full h-full rounded-2xl overflow-hidden border shadow-md"
                        style={{ backgroundColor: "var(--card-color,#ffffff)", borderColor: "var(--stroke,#e5e7eb)" }}
                    >
                        <img
                            src={slideData?.image?.__image_url__ || 'https://images.unsplash.com/photo-1497366216548-37526070297c?auto=format&fit=crop&w=1200&q=80'}
                            alt={slideData?.image?.__image_prompt__ || 'modern office'}
                            className="w-full h-full object-cover"
                        />
                    </div>
                </div>
            </div>
        </div>
    </>
)

export default SectionOverviewSlideLayout
