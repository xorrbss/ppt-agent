import * as z from 'zod';
import React from 'react';

export const Schema = z.object({
    title: z.string().describe('The main heading of the slide').default('우리 팀 구성원'),
    subtitle: z.string().max(300).describe('A descriptive sub-heading explaining the team\'s focus').default('금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 CAC를 150달러 미만으로 유지하며 350만 달러의 신규 파이프라인을 목표로 합니다.'),
    teamMembers: z.array(z.object({
        name: z.string().max(30).describe('Name of the team member'),
        designation: z.string().max(40).describe('Job title or role of the team member'),
        image: z.object({
            __image_url__: z.string(),
            __image_prompt__: z.string().max(100)
        }).describe('Profile picture of the team member'),
        summary: z.string().max(100).describe('Short summary or focus area of the team member')
    })).max(4).describe('List of team members').default([
        {
            name: '김서연',
            designation: '창업자 & CEO',
            image: {
                __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
                __image_prompt__: 'Professional headshot of a female executive smiling'
            },
            summary: '직원 500명 이상 기업에 집중합니다.'
        },
        {
            name: '김서연',
            designation: '창업자 & CEO',
            image: {
                __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
                __image_prompt__: 'Professional headshot of a female executive smiling'
            },
            summary: '직원 500명 이상 기업에 집중합니다.'
        },
        {
            name: '김서연',
            designation: '창업자 & CEO',
            image: {
                __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
                __image_prompt__: 'Professional headshot of a female executive smiling'
            },
            summary: '직원 500명 이상 기업에 집중합니다.'
        },
        {
            name: '김서연',
            designation: '창업자 & CEO',
            image: {
                __image_url__: 'https://presenton-public-assets.s3.ap-southeast-1.amazonaws.com/replaceable_template_image.png',
                __image_prompt__: 'Professional headshot of a female executive smiling'
            },
            summary: '직원 500명 이상 기업에 집중합니다.'
        }
    ]),
});

export const layoutId = 'title-subtitle-four-team-member-cards';
export const layoutName = '제목 부제목 4인 팀 구성원 카드';
export const layoutDescription = '상단에 가운데 정렬된 제목과 설명 부제목을 두고 그 아래에 네 개의 가로형 팀 구성원 카드를 배치한 전문적인 팀 소개 슬라이드입니다. 각 카드에는 구성원의 이름, 직책, 프로필 이미지, 간단한 요약이 표시됩니다. 리더십 팀, 프로젝트 구성원, 자문위원회 또는 역할과 담당 영역과 함께 핵심 인력을 소개하는 데 적합합니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, subtitle, teamMembers } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex flex-col "

                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Albert Sans)',
                }}
            >
                {/* Header Section */}
                <div className="flex flex-col items-center text-center mb-16 px-12 pt-12">
                    <h1 className="text-[42.7px] font-bold tracking-[-1.6px] mb-4"

                        style={{ color: 'var(--background-text,#000000)' }}
                    >
                        {title}
                    </h1>
                    <p className="text-[16px]  leading-[1.6] max-w-[800px]"

                        style={{ color: 'var(--background-text,#000000)' }}
                    >
                        {subtitle}
                    </p>
                </div>

                {/* Cards Section */}
                <div className="flex justify-center items-start gap-[43px] flex-grow px-12">
                    {teamMembers?.map((member, index) => (
                        <div key={index} className="flex flex-col bg-[#BEF4FE] rounded-[11.3px] border-[0.7px] border-[#EBEBEB] w-[214.6px] h-[339.8px] overflow-hidden"

                            style={{
                                backgroundColor: 'var(--primary-color,#BEF4FE)',
                                borderColor: 'var(--stroke,#EBEBEB)',
                            }}
                        >
                            <div className="flex flex-col items-center justify-center py-4 px-2 min-h-[64px]">
                                <span className="text-[17.8px]  tracking-[-0.1px] line-clamp-1"

                                    style={{ color: 'var(--primary-text,#000000)' }}
                                >
                                    {member?.name}
                                </span>
                                <span className="text-[14.2px]  tracking-[-0.1px] line-clamp-1"

                                    style={{ color: 'var(--primary-text,#55626E)' }}
                                >
                                    {member?.designation}
                                </span>
                            </div>

                            <div className="w-full h-[214.6px]">
                                <img
                                    src={member?.image?.__image_url__}
                                    alt={member?.name}
                                    className="w-full h-full object-cover"
                                />
                            </div>

                            <div className="flex-grow flex items-center justify-center text-center p-3">
                                <span className="text-[16px]  leading-[1.2] line-clamp-2"

                                    style={{ color: 'var(--primary-text,#000000)' }}
                                >
                                    {member?.summary}
                                </span>
                            </div>
                        </div>
                    ))}
                </div>

                {/* Footer Section */}
                <div className="flex items-center px-[72px] w-full absolute bottom-4 ">
                    {((data as any)?.__companyName__ || (data as any)?._logo_url__) && <div className="flex items-center gap-1 mr-1">
                        {(data as any)?._logo_url__ && <img src={(data as any)?._logo_url__} alt="logo" className="w-[60px] object-contain" />}
                        <span
                            style={{ backgroundColor: 'var(--stroke, #F0F0F0)' }}
                            className=' w-[2px] h-4'></span>
                        {(data as any)?.__companyName__ && <span className="text-sm  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                            {(data as any)?.__companyName__ || '회사명'}
                        </span>}
                    </div>}
                    <div className="flex-1 h-[3.6px] bg-[#55626E]"

                        style={{ backgroundColor: 'var(--background-text,#55626E)' }}
                    />
                    <div className="relative ml-[-4px] w-[58px] h-[58px] flex items-center justify-center">
                        <div className="w-[41px] h-[41px] bg-[#4D5463] rotate-45"

                            style={{ backgroundColor: 'var(--background-text,#4D5463)' }}
                        />
                    </div>
                </div>
            </div>

        </>
    );
};

export default dynamicSlideLayout;

