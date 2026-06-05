import React from 'react'
import * as z from "zod";
import { ImageSchema } from '../defaultSchemes';

export const layoutId = 'general-intro-slide'
export const layoutName = '소개 슬라이드'
export const layoutDescription = '제목, 설명 텍스트, 발표자 정보, 보조 이미지로 구성된 깔끔한 슬라이드 레이아웃입니다.'

const introSlideSchema = z.object({
    title: z.string().min(3).max(40).default('제품 개요').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(150).default('저희 제품은 실시간 보고와 데이터 기반 의사결정을 위한 맞춤형 대시보드를 제공합니다. 서드파티 도구와 연동되어 업무를 강화하고, 비즈니스 성장에 맞춰 확장되어 효율성을 높입니다.').meta({
        description: "Main description text content",
    }),
    presenterName: z.string().min(2).max(50).default('홍길동').meta({
        description: "Name of the presenter",
    }),
    presentationDate: z.string().min(2).max(50).default('2025년 12월').meta({
        description: "Date of the presentation must be the latest date like today's date",
    }),
    image: ImageSchema.default({
        __image_url__: 'https://images.unsplash.com/photo-1552664730-d307ca884978?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=1000&q=80',
        __image_prompt__: 'Business team in meeting room discussing product features and solutions'
    }).meta({
        description: "Supporting image for the slide",
    })
})

export const Schema = introSlideSchema

export type IntroSlideData = z.infer<typeof introSlideSchema>

interface IntroSlideLayoutProps {
    data?: Partial<IntroSlideData>
}

const IntroSlideLayout: React.FC<IntroSlideLayoutProps> = ({ data: slideData }) => {
    // Generate initials from presenter name
    const getInitials = (name: string) => {
        return name.split(' ').map(word => word.charAt(0).toUpperCase()).join('');
    };

    const presenterInitials = getInitials(slideData?.presenterName || '홍길동');
    return (
        <>

            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div
                className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white relative z-20 mx-auto overflow-hidden"
                style={{
                    background: "var(--background-color,#ffffff)"
                    , fontFamily: "var(--heading-font-family,Poppins)"
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


                {/* Main Content */}
                <div className="relative z-10 flex h-full px-8 sm:px-12 lg:px-20 pt-12 pb-8">
                    {/* Left Section - Image */}
                    <div className="flex-1 flex items-center justify-center pr-8">
                        <div className="w-full max-w-lg h-80 rounded-2xl overflow-hidden shadow-lg">
                            <img
                                src={slideData?.image?.__image_url__ || ''}
                                alt={slideData?.image?.__image_prompt__ || slideData?.title || ''}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    </div>

                    {/* Right Section - Content */}
                    <div className="flex-1 flex flex-col justify-center pl-8 space-y-6">
                        {/* Title */}
                        <h1 style={{ color: "var(--background-text,#111827)" }} className="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900 leading-tight">
                            {slideData?.title || '제품 개요'}
                        </h1>

                        {/* Purple accent line */}
                        <div style={{ background: "var(--background-text,#9333ea)" }} className="w-20 h-1 bg-purple-600"></div>

                        {/* Description */}
                        <p style={{ color: "var(--background-text,#4b5563)" }} className="text-base sm:text-lg text-gray-700 leading-relaxed">
                            {slideData?.description || '저희 제품은 실시간 보고와 데이터 기반 의사결정을 위한 맞춤형 대시보드를 제공합니다. 서드파티 도구와 연동되어 업무를 강화하고, 비즈니스 성장에 맞춰 확장되어 효율성을 높입니다.'}
                        </p>

                        {/* Presenter Section */}
                        <div className="bg-white/50 backdrop-blur-sm rounded-lg p-4 lg:p-6 border border-gray-200 shadow-sm"
                            style={{
                                backgroundColor: 'var(--card-color, #ffffff)',
                                borderColor: 'var(--stroke, #e5e7eb)',
                            }}
                        >
                            <div className="flex items-center gap-4">
                                {/* Custom Initials Icon */}
                                <div style={{ background: "var(--primary-color,#9333ea)" }} className="w-10 h-10 lg:w-12 lg:h-12 bg-purple-600 rounded-full flex items-center justify-center">
                                    <span className="font-bold text-sm lg:text-base" style={{ color: "var(--primary-text,#FFFFFF)" }}>
                                        {presenterInitials}
                                    </span>
                                </div>

                                {/* Presenter Info */}
                                <div className="flex flex-col">
                                    <span style={{ color: "var(--background-text,#111827)" }} className="text-lg lg:text-xl font-bold text-gray-900">
                                        {slideData?.presenterName || '홍길동'}
                                    </span>
                                    <span style={{ color: "var(--background-text,#4b5563)" }} className="text-sm lg:text-base text-gray-600 font-medium">
                                        {slideData?.presentationDate || '2025년 12월'}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </>
    )
}

export default IntroSlideLayout 