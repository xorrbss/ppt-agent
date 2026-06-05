import React from 'react'
import * as z from "zod";
import { ImageSchema } from '../defaultSchemes';

export const layoutId = 'quote-slide'
export const layoutName = '이미지 오버레이 위 가운데 텍스트'
export const layoutDescription = '배경 이미지, 반투명 오버레이, 강조선이 있는 가운데 정렬 헤더, 큰 인용 아이콘, 인용문, 그리고 장식선이 있는 저자 표기로 구성된 전체 화면 레이아웃입니다.'

const quoteSlideSchema = z.object({
    heading: z.string().min(3).max(60).default('지혜의 말').meta({
        description: "Heading text of the slide",
    }),
    quote: z.string().min(10).max(200).default('성공은 끝이 아니고 실패는 치명적이지 않습니다. 중요한 것은 계속 나아가는 용기입니다. 미래는 자신의 꿈의 아름다움을 믿는 사람들의 것입니다.').meta({
        description: "Quotation text displayed on the slide",
    }),
    author: z.string().min(2).max(50).default('윈스턴 처칠').meta({
        description: "Attribution name for the quote",
    }),
    backgroundImage: ImageSchema.default({
        __image_url__: 'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=2000&q=80',
        __image_prompt__: 'Inspirational mountain landscape with dramatic sky and clouds'
    }).meta({
        description: "URL of the background image",
    })
})

export const Schema = quoteSlideSchema

export type QuoteSlideData = z.infer<typeof quoteSlideSchema>

interface QuoteSlideLayoutProps {
    data?: Partial<QuoteSlideData>
}

const QuoteSlideLayout: React.FC<QuoteSlideLayoutProps> = ({ data: slideData }) => {
    return (
        <>
            {/* Import Google Fonts */}
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />

            <div
                className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white relative z-20 mx-auto overflow-hidden"
                style={{
                    fontFamily: 'var(--heading-font-family,Poppins)',
                    background: "var(--background-color,#ffffff)"
                }}
            >
                {((slideData as any)?.__companyName__ || (slideData as any)?._logo_url__) && (
                    <div className="absolute top-0 left-0 right-0 px-8  pt-4">
                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-1">
                                {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-[60px] object-contain" />}
                                <span
                                    style={{ backgroundColor: 'var(--stroke, #F0F0F0)' }}
                                    className=' w-[2px] h-4'></span>
                                {(slideData as any)?.__companyName__ && <span className="text-sm  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                                    {(slideData as any)?.__companyName__ || '회사명'}
                                </span>}
                            </div>
                        </div>
                    </div>
                )}
                {/* Background Image */}
                <div
                    className="absolute inset-0 w-full h-full bg-cover bg-center bg-no-repeat"
                    style={{
                        backgroundImage: `url('${slideData?.backgroundImage?.__image_url__ || ''}')`,
                    }}
                />

                {/* Background Overlay - low opacity primary accent */}
                <div
                    className="absolute inset-0"
                    style={{ backgroundColor: 'var(--background-color, #000000)', opacity: 0.5 }}
                ></div>

                {/* Decorative Elements */}
                <div className="absolute top-0 left-0 w-32 h-32 bg-purple-600/20 rounded-full blur-3xl"></div>
                <div className="absolute bottom-0 right-0 w-40 h-40 bg-purple-400/20 rounded-full blur-3xl"></div>
                <div className="absolute top-1/2 left-1/4 w-24 h-24 bg-white/10 rounded-full blur-2xl"></div>

                {/* Main Content */}
                <div className="relative z-10 px-8 sm:px-12 lg:px-20 pt-14 py-12 flex-1 flex flex-col justify-center h-full">
                    <div className="text-center space-y-8 max-w-4xl mx-auto">

                        {/* Heading */}
                        <div className="space-y-4">
                            <h1 style={{ color: "var(--background-text,#ffffff)" }} className="text-3xl sm:text-4xl lg:text-5xl font-bold text-white leading-tight">
                                {slideData?.heading || '지혜의 말'}
                            </h1>
                            {/* Purple accent line */}
                            <div style={{ background: "var(--primary-color,#9333ea)" }} className="w-20 h-1 bg-purple-400 mx-auto"></div>
                        </div>

                        {/* Quote Section */}
                        <div className="space-y-6">
                            {/* Quote Icon */}
                            <div className="flex justify-center">
                                <svg
                                    className="w-12 h-12 text-purple-300 opacity-80" style={{ color: "var(--primary-color,#9333ea)" }}
                                    fill="currentColor"
                                    viewBox="0 0 24 24"
                                >
                                    <path d="M14.017 21v-7.391c0-5.704 3.731-9.57 8.983-10.609l.995 2.151c-2.432.917-3.995 3.638-3.995 5.849h4v10h-9.983zm-14.017 0v-7.391c0-5.704 3.748-9.57 9-10.609l.996 2.151c-2.433.917-3.996 3.638-3.996 5.849h3.983v10h-9.983z" />
                                </svg>
                            </div>

                            {/* Quote Text */}
                            <blockquote style={{ color: "var(--background-text,#ffffff)" }} className="text-xl sm:text-2xl lg:text-3xl font-medium text-white leading-relaxed italic">
                                "{slideData?.quote || '성공은 끝이 아니고 실패는 치명적이지 않습니다. 중요한 것은 계속 나아가는 용기입니다. 미래는 자신의 꿈의 아름다움을 믿는 사람들의 것입니다.'}"
                            </blockquote>

                            {/* Author */}
                            <div className="flex justify-center items-center space-x-4">
                                <div style={{ background: "var(--primary-color,#9333ea)" }} className="w-16 h-px bg-purple-300"></div>
                                <cite className="text-base sm:text-lg text-purple-200 font-semibold not-italic"

                                    style={{
                                        color: 'var(--background-text,#ffffff)'
                                    }}
                                >
                                    {slideData?.author || '윈스턴 처칠'}
                                </cite>
                                <div style={{ background: "var(--primary-color,#9333ea)" }} className="w-16 h-px bg-purple-300"></div>
                            </div>
                        </div>
                    </div>
                </div>


            </div>
        </>
    )
}

export default QuoteSlideLayout 