import React from 'react'
import * as z from "zod";
import { ImageSchema } from '../defaultSchemes';

export const layoutId = 'team-slide'
export const layoutName = '사진 카드 그리드가 있는 설명'
export const layoutDescription = '왼쪽에 제목, 강조선, 설명을 배치하고 오른쪽에 2~4개의 인물 카드를 2x2 또는 유연한 그리드로 배치한 2개 섹션 레이아웃입니다. 각 카드는 사진, 이름, 직책, 약력을 표시합니다.'

const teamMemberSchema = z.object({
    name: z.string().min(2).max(50).meta({
        description: "Name of the person"
    }),
    position: z.string().min(2).max(50).meta({
        description: "Role or position title"
    }),
    description: z.string().max(150).meta({
        description: "Brief description text"
    }),
    image: ImageSchema
});

const teamSlideSchema = z.object({
    title: z.string().min(3).max(40).default('우리 팀원').meta({
        description: "Heading text of the slide",
    }),
    companyDescription: z.string().min(10).max(150).default('지냐드 인터내셔널은 기업을 위한 혁신적인 맞춤형 디지털 솔루션을 선도적으로 제공합니다. 우리의 사명은 최첨단 기술과 전략적 파트너십을 통해 조직이 목표를 달성하도록 지원하는 것입니다.').meta({
        description: "Supporting description text",
    }),
    teamMembers: z.array(teamMemberSchema).min(2).max(4).default([
        {
            name: '김서연',
            position: 'CEO',
            description: '디지털 전환과 비즈니스 성장 분야에서 15년 이상의 경험을 가진 전략적 리더.',
            image: {
                __image_url__: 'https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80',
                __image_prompt__: 'Professional businesswoman CEO headshot'
            }
        },
        {
            name: '박지훈',
            position: 'CTO',
            description: '확장 가능한 솔루션과 혁신적인 소프트웨어 아키텍처를 전문으로 하는 기술 전문가.',
            image: {
                __image_url__: 'https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80',
                __image_prompt__: 'Professional businessman CTO headshot'
            }
        },
        {
            name: '최민준',
            position: 'COO',
            description: '효율성, 프로세스 최적화, 팀 개발에 집중하는 운영 리더.',
            image: {
                __image_url__: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80',
                __image_prompt__: 'Professional businessman COO headshot'
            }
        },
        {
            name: '이하나',
            position: 'CMO',
            description: '브랜드 개발과 고객 참여에 전문성을 갖춘 마케팅 전략가.',
            image: {
                __image_url__: 'https://images.unsplash.com/photo-1438761681033-6461ffad8d80?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80',
                __image_prompt__: 'Professional businesswoman CMO headshot'
            }
        }
    ]).meta({
        description: "List of person cards with their information",
    })
})

export const Schema = teamSlideSchema

export type TeamSlideData = z.infer<typeof teamSlideSchema>

interface TeamSlideLayoutProps {
    data?: Partial<TeamSlideData>
}

const TeamSlideLayout: React.FC<TeamSlideLayoutProps> = ({ data: slideData }) => {
    const teamMembers = slideData?.teamMembers || []

    // Function to determine grid classes based on number of team members
    const getGridClasses = (count: number) => {
        if (count <= 2) {
            return 'grid-cols-1 gap-6'
        } else if (count <= 4) {
            return 'grid-cols-2 gap-6'
        } else {
            return 'grid-cols-2 lg:grid-cols-3 gap-4'
        }
    }

    return (
        <> <link
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
                {/* Decorative Wave Pattern */}
                <div className="absolute bottom-0 left-0 w-80 h-40 opacity-10 overflow-hidden">
                    <svg className="w-full h-full" viewBox="0 0 300 150" fill="none">
                        <path d="M0 75C75 50 150 100 225 75C262.5 62.5 300 75 300 75V150H0V75Z" fill="#8b5cf6" opacity="0.3" />
                        <path d="M0 100C100 125 200 75 300 100V125C225 112.5 150 125 75 112.5L0 100Z" fill="#8b5cf6" opacity="0.2" />
                    </svg>
                </div>

                {/* Main Content */}
                <div className="relative z-10 flex h-full px-8 sm:px-12 lg:px-20 pt-12 pb-8">
                    {/* Left Section - Title and Company Description */}
                    <div className="flex-1 flex flex-col justify-center pr-8 space-y-6">
                        {/* Title */}
                        <h1 style={{ color: "var(--background-text,#111827)" }} className="text-[42.7px] font-bold text-gray-900 leading-tight">
                            {slideData?.title || '우리 팀원'}
                        </h1>

                        {/* Purple accent line */}
                        <div style={{ background: "var(--primary-color,#9333ea)" }} className="w-20 h-1 bg-purple-600"></div>

                        {/* Company Description */}
                        <p style={{ color: "var(--background-text,#4b5563)" }} className="text-base sm:text-lg text-gray-700 leading-relaxed">
                            {slideData?.companyDescription || '지냐드 인터내셔널은 기업을 위한 혁신적인 맞춤형 디지털 솔루션을 선도적으로 제공합니다. 우리의 사명은 최첨단 기술과 전략적 파트너십을 통해 조직이 목표를 달성하도록 지원하는 것입니다.'}
                        </p>
                    </div>

                    {/* Right Section - Team Members Grid */}
                    <div className="flex-1 flex items-center justify-center pl-8">
                        <div className={`grid ${getGridClasses(teamMembers.length)} w-full max-w-2xl`}>
                            {teamMembers.map((member, index) => (
                                <div key={index} className="text-center space-y-3">
                                    {/* Member Photo */}
                                    <div className="w-32 h-32 mx-auto rounded-lg overflow-hidden shadow-md" style={{ background: "var(--card-color,#e5e7eb)" }}>
                                        <img
                                            src={member.image.__image_url__ || ''}
                                            alt={member.image.__image_prompt__ || member.name}
                                            className="w-full h-full object-cover"
                                        />
                                    </div>

                                    {/* Member Info */}
                                    <div>
                                        <h3 style={{ color: "var(--background-text,#111827)" }} className="text-lg font-semibold text-gray-900">
                                            {member.name}
                                        </h3>
                                        <p style={{ color: "var(--background-text,#4b5563)" }} className="text-sm font-medium text-gray-600 italic mb-2">
                                            {member.position}
                                        </p>
                                        <p style={{ color: "var(--background-text,#4b5563)" }} className="text-xs text-gray-600 leading-relaxed px-2">
                                            {member.description}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </>
    )
}

export default TeamSlideLayout 