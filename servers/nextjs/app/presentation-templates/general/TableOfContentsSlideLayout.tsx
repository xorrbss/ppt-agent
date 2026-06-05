import React from 'react'
import * as z from "zod";

export const layoutId = 'table-of-contents-slide'
export const layoutName = '목차'
export const layoutDescription = '번호가 매겨진 섹션과 페이지 참조가 있는 전문적인 목차 레이아웃입니다. 사용하는 경우 소개 슬라이드 바로 다음에 배치해야 합니다.'

const tableOfContentsSlideSchema = z.object({
    sections: z.array(z.object({
        number: z.number().min(1).meta({
            description: "Section number"
        }),
        title: z.string().min(1).max(80).meta({
            description: "Section title"
        }),
        pageNumber: z.string().min(1).max(10).meta({
            description: "Page number for this section"
        })
    })).default([
        { number: 1, title: "문제", pageNumber: "03" },
        { number: 2, title: "솔루션", pageNumber: "04" },
        { number: 3, title: "제품 개요", pageNumber: "05" },
        { number: 4, title: "시장 규모", pageNumber: "06" },
        { number: 5, title: "시장 검증", pageNumber: "07" },
        { number: 6, title: "회사 성장세", pageNumber: "08" },
        { number: 7, title: "제품 성과", pageNumber: "09" },
        { number: 8, title: "비즈니스 모델", pageNumber: "10" },
        { number: 9, title: "경쟁 우위", pageNumber: "11" },
        { number: 10, title: "팀 구성원", pageNumber: "12" }
    ]).meta({
        description: "List of table of contents sections",
    })
})

export const Schema = tableOfContentsSlideSchema

export type TableOfContentsSlideData = z.infer<typeof tableOfContentsSlideSchema>

interface TableOfContentsSlideLayoutProps {
    data?: Partial<TableOfContentsSlideData>
}

const TableOfContentsSlideLayout: React.FC<TableOfContentsSlideLayoutProps> = ({ data: slideData }) => {
    const sections = slideData?.sections || []
    const midPoint = Math.ceil(sections.length / 2)
    const leftSections = sections.slice(0, midPoint)
    const rightSections = sections.slice(midPoint)

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />

            <div
                className="w-full rounded-sm max-w-[1280px] shadow-lg px-8 sm:px-12 lg:px-20 py-8 sm:py-12 lg:py-16 max-h-[720px] aspect-video bg-white relative z-20 mx-auto"
                style={{
                    fontFamily: 'var(--heading-font-family,Poppins)',
                    background: "var(--background-color,#ffffff)"
                }}
            >
                {((slideData as any)?.__companyName__ || (slideData as any)?._logo_url__) && (
                    <div className="absolute top-0 left-0 right-0 px-8 sm:px-12 lg:px-20 pt-4">
                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-1">

                                {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
                                {(slideData as any)?.__companyName__ && <span className="text-sm sm:text-base font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                                    {(slideData as any)?.__companyName__ || '회사명'}
                                </span>}
                            </div>
                        </div>
                    </div>
                )}

                {/* Title Section */}
                <div className="text-center mb-8 sm:mb-12 mt-6">
                    <h1 style={{ color: "var(--background-text,#111827)" }} className="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900 mb-4">
                        목차
                    </h1>
                    {/* Decorative Wave */}
                    <div className="flex justify-center">
                        <svg width="80" height="20" viewBox="0 0 80 20" className="text-purple-600" style={{ color: "var(--primary-color,#9333ea)" }}>
                            <path
                                d="M0 10 Q20 0 40 10 T80 10"
                                stroke="currentColor"
                                strokeWidth="3"
                                fill="none"
                            />
                        </svg>
                    </div>
                </div>

                {/* Content Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 sm:gap-8 lg:gap-12">
                    {/* Left Column */}
                    <div className="space-y-4 sm:space-y-6">
                        {leftSections.map((section) => (
                            <div key={section.number} className="flex items-center justify-between group">
                                <div className="flex items-center space-x-4">
                                    {/* Number Box */}
                                    <div style={{ background: "var(--primary-color,#9333ea)", color: "var(--primary-text,#ffffff)" }} className="w-12 h-12 sm:w-14 sm:h-14 bg-purple-600 rounded-xl flex items-center justify-center text-white font-bold text-lg sm:text-xl group-hover:bg-purple-700 transition-colors">
                                        {section.number}
                                    </div>
                                    {/* Title */}
                                    <span style={{ color: "var(--background-text,#111827)" }} className="text-lg sm:text-xl font-medium text-gray-800 group-hover:text-purple-600 transition-colors">
                                        {section.title}
                                    </span>
                                </div>
                                {/* Page Number */}
                                <div className="text-right">
                                    <span style={{ color: "var(--background-text,#4b5563)" }} className="text-lg sm:text-xl text-gray-600">
                                        {section.pageNumber}
                                    </span>
                                    {/* Dotted line effect */}
                                    <div style={{ color: "var(--background-text,#4b5563)" }} className="text-gray-300 text-sm mt-1">
                                        .....
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Right Column */}
                    <div className="space-y-4 sm:space-y-6">
                        {rightSections.map((section) => (
                            <div key={section.number} className="flex items-center justify-between group">
                                <div className="flex items-center space-x-4">
                                    {/* Number Box */}
                                    <div style={{ background: "var(--primary-color,#9333ea)", color: "var(--primary-text,#ffffff)" }} className="w-12 h-12 sm:w-14 sm:h-14 bg-purple-600 rounded-xl flex items-center justify-center text-white font-bold text-lg sm:text-xl group-hover:bg-purple-700 transition-colors">
                                        {section.number}
                                    </div>
                                    {/* Title */}
                                    <span style={{ color: "var(--background-text,#111827)" }} className="text-lg sm:text-xl font-medium text-gray-800 group-hover:text-purple-600 transition-colors">
                                        {section.title}
                                    </span>
                                </div>
                                {/* Page Number */}
                                <div className="text-right">
                                    <span style={{ color: "var(--background-text,#4b5563)" }} className="text-lg sm:text-xl text-gray-600">
                                        {section.pageNumber}
                                    </span>
                                    {/* Dotted line effect */}
                                    <div style={{ color: "var(--background-text,#4b5563)" }} className="text-gray-300 text-sm mt-1">
                                        .....
                                    </div>
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