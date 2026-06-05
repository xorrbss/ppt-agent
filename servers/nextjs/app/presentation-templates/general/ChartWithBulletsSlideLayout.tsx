"use client";

import React from 'react'
import * as z from "zod";
import { IconSchema } from '../defaultSchemes';
import { RemoteSvgIcon } from '@/app/hooks/useRemoteSvgIcon';
import { GeneralChart } from "./GeneralChartPrimitives";

export const layoutId = 'chart-with-bullets-slide'
export const layoutName = '글머리 박스가 있는 차트'
export const layoutDescription = '제목, 설명, 왼쪽 차트와 오른쪽에 아이콘이 있는 색상 글머리 박스로 구성된 슬라이드 레이아웃입니다. 데이터가 있는 경우에만 선택하세요.'

const barPieLineAreaChartDataSchema = z.object({
    type: z.union([z.literal('bar'), z.literal('pie'), z.literal('line'), z.literal('area')]),
    data: z.array(z.object({
        name: z.string().meta({ description: "Data point name" }),
        value: z.number().meta({ description: "Data point value" }),
    })).min(2).max(5)
})

const scatterChartDataSchema = z.object({
    type: z.literal('scatter'),
    data: z.array(z.object({
        x: z.number().meta({ description: "X coordinate" }),
        y: z.number().meta({ description: "Y coordinate" }),
    })).min(2).max(20)
})

const chartWithBulletsSlideSchema = z.object({
    title: z.string().min(3).max(40).default('시장 규모').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(150).default('기업은 노후화된 기술과 비용 상승이라는 과제에 직면하여, 경쟁 시장에서 효율성과 성장이 제한되고 있습니다.').meta({
        description: "Description text below the title",
    }),
    chartData: z.union([barPieLineAreaChartDataSchema, scatterChartDataSchema]).default({
        type: 'bar',
        data: [
            { name: 'Q1', value: 5 },
            { name: 'Q1', value: 5 },
            { name: 'Q1', value: 5 },
        ]
    }
    ),

    showLegend: z.boolean().default(false).meta({
        description: "Whether to show chart legend",
    }),
    showTooltip: z.boolean().default(true).meta({
        description: "Whether to show chart tooltip",
    }),
    bulletPoints: z.array(z.object({
        title: z.string().min(2).max(80).meta({
            description: "Bullet point title",
        }),
        description: z.string().min(10).max(150).meta({
            description: "Bullet point description",
        }),
        icon: IconSchema,
    })).min(1).max(3).default([
        {
            title: '전체 시장 규모(TAM)',
            description: '기업은 TAM을 활용하여 향후 확장과 투자를 계획할 수 있습니다.',
            icon: {
                __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/chart-line-up-bold.svg',
                __icon_query__: 'target market scope'
            }
        },
        {
            title: '유효 시장(SAM)',
            description: '영업 활동을 위한 보다 측정 가능한 시장 세그먼트를 나타냅니다.',
            icon: {
                __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/chart-line-up-bold.svg',
                __icon_query__: 'pie chart analysis'
            }
        },
        {
            title: '수익 시장(SOM)',
            description: '기업이 시장에 맞춰 개발 전략을 계획하도록 돕습니다.',
            icon: {
                __icon_url__: 'https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/chart-line-up-bold.svg',
                __icon_query__: 'trending up growth'
            }
        }
    ]).meta({
        description: "List of bullet points with colored boxes and icons",
    })
})

export const Schema = chartWithBulletsSlideSchema


export type ChartWithBulletsSlideData = z.infer<typeof chartWithBulletsSlideSchema>

interface ChartWithBulletsSlideLayoutProps {
    data?: Partial<ChartWithBulletsSlideData>
}


const ChartWithBulletsSlideLayout: React.FC<ChartWithBulletsSlideLayoutProps> = ({ data: slideData }) => {
    const chartData = slideData?.chartData?.data || [];
    const chartType = slideData?.chartData?.type;
    const showLegend = slideData?.showLegend || false;
    const showTooltip = slideData?.showTooltip !== false;
    const bulletPoints = slideData?.bulletPoints || []

    return (
        <>


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
                {/* Main Content */}
                <div className="flex h-full px-8 sm:px-12 lg:px-20 pt-8 pb-8">
                    {/* Left Section - Title, Description, Chart */}
                    <div className="flex-1 flex flex-col pr-8">
                        {/* Title */}
                        <h1 style={{ color: "var(--background-text, #111827)" }} className="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900 mb-4">
                            {slideData?.title || '시장 규모'}
                        </h1>

                        {/* Description */}
                        <p style={{ color: "var(--background-text, #4b5563)" }} className="text-base text-gray-700 leading-relaxed mb-8">
                            {slideData?.description || '기업은 노후화된 기술과 비용 상승이라는 과제에 직면하여, 경쟁 시장에서 효율성과 성장이 제한되고 있습니다.'}
                        </p>

                        {/* Chart Container */}
                        <div className="flex-1 min-h-0 overflow-hidden rounded-lg shadow-sm border border-gray-100 p-4"
                            style={{
                                borderColor: 'var(--stroke, #F8F9FA)',
                            }}
                        >
                            <div className="h-full max-h-[460px] min-h-0 w-full overflow-hidden">
                                <GeneralChart
                                    type={chartType}
                                    data={chartData}
                                    showLegend={showLegend}
                                    showTooltip={showTooltip}
                                />
                            </div>
                        </div>
                    </div>

                    {/* Right Section - Bullet Point Boxes */}
                    <div className="flex-shrink-0 w-80 flex flex-col justify-center space-y-4">
                        {bulletPoints.map((bullet, index) => (
                            <div
                                key={index}
                                className="rounded-2xl p-6 text-white"
                                style={{
                                    backgroundColor: 'var(--primary-color,#9333ea)'
                                }}
                            >
                                {/* Icon and Title */}
                                <div className="flex items-center space-x-3 mb-3">
                                    <div style={{ background: "var(--primary-color,#9333ea)" }} className="w-8 h-8 rounded-lg flex items-center justify-center">
                                        <RemoteSvgIcon
                                            url={bullet.icon.__icon_url__}
                                            strokeColor={"currentColor"}
                                            className="w-5 h-5"
                                            color="var(--primary-text, #ffffff)"
                                            title={bullet.icon.__icon_query__}
                                        />
                                    </div>
                                    <h3 style={{ color: "var(--primary-text, #ffffff)" }} className="text-lg font-semibold">
                                        {bullet.title}
                                    </h3>
                                </div>

                                {/* Description */}
                                <p style={{ color: "var(--primary-text, #ffffff)" }} className="text-sm leading-relaxed opacity-90">
                                    {bullet.description}
                                </p>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </>
    )
}

export default ChartWithBulletsSlideLayout
