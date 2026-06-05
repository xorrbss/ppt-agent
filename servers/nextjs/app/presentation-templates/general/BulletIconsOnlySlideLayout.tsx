import React from 'react'
import * as z from "zod";
import { ImageSchema, IconSchema } from '../defaultSchemes';
import { RemoteSvgIcon } from '@/app/hooks/useRemoteSvgIcon';

export const layoutId = 'bullet-icons-only-slide'
export const layoutName = '아이콘 글머리만'
export const layoutDescription = '제목, 아이콘이 있는 글머리 격자(제목과 설명), 보조 이미지로 구성된 슬라이드 레이아웃입니다.'

const bulletIconsOnlySlideSchema = z.object({
    title: z.string().min(3).max(40).default('솔루션').meta({
        description: "Main title of the slide",
    }),
    image: ImageSchema.default({
        __image_url__: 'https://images.unsplash.com/photo-1552664730-d307ca884978?ixlib=rb-4.0.3&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D&auto=format&fit=crop&w=1000&q=80',
        __image_prompt__: 'Business professionals collaborating and discussing solutions'
    }).meta({
        description: "Supporting image for the slide",
    }),
    bulletPoints: z.array(z.object({
        title: z.string().min(2).max(80).meta({
            description: "Bullet point title",
        }),
        subtitle: z.string().min(5).max(150).optional().meta({
            description: "Optional short subtitle or brief explanation",
        }),
        icon: IconSchema,
    })).min(2).max(3).default([
        {
            title: '맞춤형 소프트웨어',
            subtitle: '프로세스를 최적화하고 효율성을 높이는 맞춤형 소프트웨어를 제작합니다.',
            icon: {
                __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/code-bold.svg',
                __icon_query__: 'code software development'
            }
        },
        {
            title: '디지털 컨설팅',
            subtitle: '저희 컨설턴트가 최신 기술을 활용하도록 조직을 안내합니다.',
            icon: {
                __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/users-four-bold.svg',
                __icon_query__: 'users consulting team'
            }
        },
        {
            title: '지원 서비스',
            subtitle: '비즈니스가 적응하고 성과를 유지할 수 있도록 지속적인 지원을 제공합니다.',
            icon: {
                __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/headphones-bold.svg',
                __icon_query__: 'headphones support service'
            }
        },
        {
            title: '확장 가능한 마케팅',
            subtitle: '데이터 기반 전략으로 비즈니스의 도달 범위와 참여도를 확장하도록 돕습니다.',
            icon: {
                __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/code-bold.svg',
                __icon_query__: 'trending up marketing growth'
            }
        }
    ]).meta({
        description: "List of bullet points with icons and optional subtitles",
    })
})

export const Schema = bulletIconsOnlySlideSchema

export type BulletIconsOnlySlideData = z.infer<typeof bulletIconsOnlySlideSchema>

interface BulletIconsOnlySlideLayoutProps {
    data?: Partial<BulletIconsOnlySlideData>
}

const BulletIconsOnlySlideLayout: React.FC<BulletIconsOnlySlideLayoutProps> = ({ data: slideData }) => {
    const bulletPoints = slideData?.bulletPoints || []

    // Function to determine grid classes based on number of bullets
    const getGridClasses = (count: number) => {
        if (count <= 2) {
            return 'grid-cols-1 gap-6'
        } else if (count <= 4) {
            return 'grid-cols-2 gap-6'
        } else {
            return 'grid-cols-2 lg:grid-cols-3 gap-6'
        }
    }

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
                {/* Decorative Wave Patterns */}
                <div className="absolute top-0 left-0 w-32 h-full opacity-10 overflow-hidden">
                    <svg className="w-full h-full" viewBox="0 0 100 400" fill="none">
                        <path d="M0 100C25 150 50 50 75 100C87.5 125 100 100 100 100V0H0V100Z" fill="var(--primary-color, #9333ea)" opacity="0.4" />
                        <path d="M0 200C37.5 250 62.5 150 100 200V150C75 175 50 150 25 175L0 200Z" fill="var(--primary-color, #9333ea)" opacity="0.3" />
                    </svg>
                </div>

                <div className="absolute bottom-0 left-0 w-48 h-32 opacity-10 overflow-hidden">
                    <svg className="w-full h-full" viewBox="0 0 200 100" fill="none">
                        <path d="M0 50C50 25 100 75 150 50C175 37.5 200 50 200 50V100H0V50Z" fill="var(--primary-color, #9333ea)" opacity="0.2" />
                    </svg>
                </div>

                {/* Main Content */}
                <div className="relative z-10 flex h-full px-8 sm:px-12 lg:px-20 pt-12 pb-8">
                    {/* Left Section - Title and Bullet Points */}
                    <div className="flex-1 flex flex-col pr-8">
                        {/* Title */}
                        <h1 style={{ color: "var(--background-text, #111827)" }} className="text-5xl sm:text-6xl lg:text-7xl font-bold text-gray-900 mb-8">
                            {slideData?.title || '솔루션'}
                        </h1>

                        {/* Bullet Points Grid */}
                        <div className={`grid ${getGridClasses(bulletPoints.length)} flex-1 content-center`}>
                            {bulletPoints.map((bullet, index) => (
                                <div
                                    key={index}
                                    className={`flex items-start space-x-4 p-4 rounded-lg`}
                                >
                                    {/* Icon */}
                                    <div style={{ background: "var(--primary-color,#9333ea)" }} className="flex-shrink-0 w-12 h-12 rounded-full flex items-center justify-center">
                                        <RemoteSvgIcon
                                            url={bullet.icon.__icon_url__}
                                            strokeColor={"currentColor"}
                                            className="w-6 h-6"
                                            color="var(--primary-text, #ffffff)"
                                            title={bullet.icon.__icon_query__}
                                        />
                                    </div>

                                    {/* Content */}
                                    <div className="flex-1">
                                        <h3 style={{ color: "var(--background-text, #111827)" }} className="text-lg sm:text-xl font-semibold text-gray-900 mb-1">
                                            {bullet.title}
                                        </h3>
                                        {bullet.subtitle && (
                                            <p style={{ color: "var(--background-text, #4b5563)" }} className="text-sm text-gray-700 leading-relaxed">
                                                {bullet.subtitle}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Right Section - Image */}
                    <div className="flex-shrink-0 w-96 flex items-center justify-center relative">
                        {/* Decorative Elements */}
                        <div style={{ color: "var(--primary-color,#9333ea)" }} className="absolute top-8 right-8 text-purple-600 opacity-60">
                            <svg width="32" height="32" viewBox="0 0 32 32" fill="currentColor">
                                <path d="M16 0l4.12 8.38L28 12l-7.88 3.62L16 24l-4.12-8.38L4 12l7.88-3.62L16 0z" />
                            </svg>
                        </div>

                        <div className="absolute top-16 left-8 opacity-20">
                            <svg width="80" height="20" viewBox="0 0 80 20" className="text-purple-600" style={{ color: "var(--primary-color,#9333ea)" }}>
                                <path
                                    d="M0 10 Q20 0 40 10 T80 10"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    fill="none"
                                />
                            </svg>
                        </div>

                        {/* Main Image */}
                        <div className="w-full h-80 rounded-2xl overflow-hidden shadow-lg">
                            <img
                                src={slideData?.image?.__image_url__ || ''}
                                alt={slideData?.image?.__image_prompt__ || slideData?.title || ''}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    </div>
                </div>
            </div>
        </>
    )
}

export default BulletIconsOnlySlideLayout 