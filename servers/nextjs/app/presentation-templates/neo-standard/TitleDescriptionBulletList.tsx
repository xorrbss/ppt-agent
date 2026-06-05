import * as z from 'zod';
import React from 'react';

export const Schema = z.object({
    title: z.string().max(30).describe('The main title of the slide').default('주요 시사점'),
    description: z.string().max(300).describe('The main paragraph description on the slide').default('금융 서비스, 헬스케어, 기술 분야의 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략으로 CAC 150달러 미만, 신규 파이프라인 350만 달러를 목표로 합니다.'),
    bullets: z.array(z.object({
        heading: z.string().max(40).describe('The heading for this bullet point'),
        description: z.string().max(120).describe('The description for this bullet point'),
    })).max(5).describe('A list of up to 5 bullet points, each with a heading and description').default([
        { heading: '시장 확장', description: '수요가 강한 고성장 버티컬과 지역을 우선순위로 둡니다.' },
        { heading: '고객 유지', description: '선제적 지원과 맞춤형 성공 프로그램으로 이탈을 줄입니다.' },
        { heading: '제품 혁신', description: '주요 고객 요청과 사용 데이터에 부합하는 기능을 출시합니다.' },
        { heading: '운영 효율성', description: '반복 업무를 자동화하여 전략 업무에 역량을 집중합니다.' },
        { heading: '팀 역량 강화', description: '교육과 도구에 투자하여 팀이 규모 있게 실행하도록 합니다.' },
    ]),
});

export const layoutId = 'title-description-bullet-list';
export const layoutName = '제목 설명 글머리 목록';
export const layoutDescription = '왼쪽에 주요 제목과 설명, 오른쪽에 최대 5개의 글머리를 배치한 깔끔한 2열 레이아웃입니다. 각 글머리에는 헤딩과 짧은 설명이 포함됩니다. 핵심 요약, 기능 강조, 맥락이 있는 구조화된 목록에 이상적입니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<z.infer<typeof Schema>> }> = ({ data }) => {
    const { title, description, bullets } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />
            <div
                className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white z-20 mx-auto overflow-hidden flex items-center justify-between gap-10 px-28 py-20"
                style={{
                    backgroundColor: 'var(--background-color,#FFFFFF)',
                    fontFamily: 'var(--body-font-family,Playfair Display)',
                }}
            >
                {/* Left Content Area */}
                <div className="flex flex-col basis-1/2">
                    {/* Decorative Green Line */}
                    <div
                        className="w-[116px] h-[3px] mb-4"
                        style={{ backgroundColor: 'var(--primary-color,#1F8A2E)' }}
                    />
                    <h1
                        className="text-[42.7px] font-bold leading-tight mb-8 tracking-[-1.6px]"
                        style={{ color: 'var(--background-text,#000000)' }}
                    >
                        {title}
                    </h1>
                    <p
                        className="text-[16px] leading-[28.5px] max-w-[475px]"
                        style={{ color: 'var(--background-text,#000000)' }}
                    >
                        {description}
                    </p>
                </div>

                {/* Right: Bullet list */}
                <div className="flex flex-col basis-1/2 gap-6">
                    {bullets?.map((item, index) => (
                        <div key={index} className="flex items-start gap-4">
                            <div
                                className="flex-shrink-0 w-[8px] h-[8px] rounded-full mt-[10px]"
                                style={{ backgroundColor: 'var(--primary-color,#1F8A2E)' }}
                            />
                            <div className="flex flex-col">
                                <h3
                                    className="text-[21.3px] font-bold leading-[25.6px]"
                                    style={{ color: 'var(--background-text,#000000)' }}
                                >
                                    {item.heading}
                                </h3>
                                <p
                                    className="text-[16px] leading-[19.2px] mt-1 max-w-[340px]"
                                    style={{ color: 'var(--background-text,#000000)' }}
                                >
                                    {item.description}
                                </p>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </>
    );
};

export default dynamicSlideLayout;
