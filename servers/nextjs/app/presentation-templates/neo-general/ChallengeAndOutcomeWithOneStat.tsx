/**
 * Zod Schema for Table of Content Slide
 * Defined based on the visual elements observed in the reference.
 */
import * as z from 'zod';
import React from 'react'
import { RemoteSvgIcon } from '@/app/hooks/useRemoteSvgIcon';
export const Schema = z.object({
    title: z.string().max(30).describe('The main heading of the slide').default('고객 사례 / 케이스 스냅샷'),
    challengeSectionTitle: z.string().max(12).describe('Heading for the first content section').default('과제'),
    challengeContent: z.string().max(140).describe('Descriptive text for the first section').default('12개 지역에 분산된 마케팅 운영으로 비효율적인 예산 배분과 일관성 없는 메시지가 발생했습니다. CAC가 전년 대비 43% 증가했습니다.'),
    outcomeSectionTitle: z.string().max(12).describe('Heading for the second content section').default('성과'),
    outcomePoints: z.array(z.string().max(40)).min(1).max(5).describe('List of bullet points for the second section').default([
        '6개월 내 CAC 34% 절감',
        '전 지역 운영 통합',
        '$4.2M 추가 파이프라인 창출'
    ]),
    customerName: z.string().max(15).describe('Primary name or title in the card').default('테크코프 글로벌'),
    customerSubTitle: z.string().max(26).describe('Subtitle or secondary text in the card').default('Fortune 500 기술 기업'),
    metricValue: z.string().max(6).describe('The primary metric or statistic value').default('$4.2M'),
    metricLabel: z.string().max(26).describe('Label describing the metric').default('Q4 증분 파이프라인'),
    metricIcon: z.object({
        __icon_url__: z.string(),
        __icon_query__: z.string().max(30),
    }).describe('Icon displayed with the metric').default({
        __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg',
        __icon_query__: 'circle',
    }),
});

type FormData = z.infer<typeof Schema>;

export const layoutId = 'title-challenge-outcome-customer-card';
export const layoutName = '강조 카드가 있는 2개 섹션 텍스트';
export const layoutDescription = '강조 바가 있는 제목, 제목과 설명이 있는 첫 번째 섹션, 왼쪽 두 번째 섹션의 번호 목록, 그리고 오른쪽에 이름, 부제목, 아이콘 배지, 두드러진 지표를 담은 강조 카드를 배치한 2개 섹션 레이아웃입니다.';

const dynamicSlideLayout: React.FC<{ data: Partial<FormData> }> = ({ data }) => {
    const {
        title,
        challengeSectionTitle,
        challengeContent,
        outcomeSectionTitle,
        outcomePoints,
        customerName,
        customerSubTitle,
        metricValue,
        metricLabel,
        metricIcon,
    } = data;

    return (
        <>
            <link
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />

            <div className="relative w-full h-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex flex-col p-[60px]"
                style={{
                    fontFamily: 'var(--heading-font-family,Poppins)',
                    background: "var(--background-color,#ffffff)"
                }}
            >
                {((data as any)?.__companyName__ || (data as any)?._logo_url__) && (
                    <div className="absolute top-0 left-0 right-0 px-8  pt-4">
                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-1">
                                {(data as any)?._logo_url__ && <img src={(data as any)?._logo_url__} alt="logo" className="w-[60px] object-contain" />}
                                <span
                                    style={{ backgroundColor: 'var(--stroke, #F0F0F0)' }}
                                    className=' w-[2px] h-4'></span>
                                {(data as any)?.__companyName__ && <span className="text-sm  font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                                    {(data as any)?.__companyName__ || '회사명'}
                                </span>}
                            </div>
                        </div>
                    </div>
                )}

                {/* Header */}
                <div className="mb-8">
                    <h1 className="text-[42.7px]  text-[#101828]  font-bold leading-[56px] tracking-[-2px] mb-4"
                        style={{ color: 'var(--background-text, #111827)' }}
                    >
                        {title}
                    </h1>
                    <div className="w-[116.6px] h-[5.7px] "
                        style={{ background: 'var(--primary-color, #9234EB)' }}
                    />
                </div>
                {/* Main Content Area */}
                <div className="flex w-full h-full gap-10">
                    {/* Left Column */}
                    <div className="flex-[1.2] flex flex-col">
                        {/* Challenge Section */}
                        <div className=" mb-5">
                            <h2 className="text-[21.3px]   font-normal mb-2 uppercase tracking-wide"
                                style={{
                                    color: 'var(--background-text,#737373)'
                                }}
                            >
                                {challengeSectionTitle}
                            </h2>
                            <p className="text-[23.1px]  font-normal leading-[32.3px]"
                                style={{
                                    color: 'var(--background-text,#000000)'
                                }}
                            >
                                {challengeContent}
                            </p>
                        </div>

                        {/* Outcome Section */}
                        <div>
                            <h2 className="text-[21.3px]   font-normal mb-2 uppercase tracking-wide"
                                style={{
                                    color: 'var(--background-text,#737373)'
                                }}
                            >
                                {outcomeSectionTitle}
                            </h2>
                            <div className="flex flex-col gap-1">
                                {outcomePoints?.map((point, index) => (
                                    <div key={index} className="flex text-[23.1px]   font-normal leading-[32.3px]"
                                        style={{
                                            color: 'var(--background-text,#000000)'
                                        }}
                                    >
                                        <span className="mr-2">{index + 1}.</span>
                                        <span>{point}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>

                    {/* Right Column / Customer Card */}
                    <div className="flex-1 flex items-center justify-center">
                        <div className=" p-10 rounded-xl flex flex-col items-start min-w-[420px]"
                            style={{
                                backgroundColor: 'var(--card-color,#FFFFFF)',

                            }}
                        >
                            {/* Customer Info */}
                            <div className="mb-10">
                                <h3 className="text-[28.7px]   font-bold leading-[40.2px]"
                                    style={{
                                        color: 'var(--background-text,#000000)'
                                    }}
                                >
                                    {customerName}
                                </h3>
                                <p className="text-[14.9px]   font-normal"
                                    style={{
                                        color: 'var(--background-text,#000000)'
                                    }}
                                >
                                    {customerSubTitle}
                                </p>
                            </div>

                            {/* Metric Row */}
                            <div className="flex items-start gap-4">
                                {metricIcon?.__icon_url__ && <div
                                    className="w-[56.7px] h-[56.7px] rounded-full flex items-center justify-center mt-4"
                                    style={{ backgroundColor: 'var(--primary-color, #9134EB )' }}
                                >
                                    <RemoteSvgIcon
                                        url={metricIcon?.__icon_url__}
                                        className="w-8 h-8 "
                                        color="var(--primary-text,#ffffff)"
                                        title={metricIcon?.__icon_query__}
                                    />
                                </div>}
                                <div className="flex flex-col">
                                    <span className="text-[70.1px] text-[#4D5463]  font-normal leading-[78.7px]"
                                        style={{
                                            color: 'var(--background-text,#4D5463)'
                                        }}
                                    >
                                        {metricValue}
                                    </span>
                                    <span className="text-[17.4px] text-[#4D5463]  font-normal leading-[22px] max-w-[180px]"
                                        style={{
                                            color: 'var(--background-text,#4D5463)'
                                        }}
                                    >
                                        {metricLabel}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>
        </>
    );
};

export default dynamicSlideLayout;