import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';


export const layoutId = 'statistics-slide'
export const layoutName = 'Statistics Slide'
export const layoutDescription = 'A slide with a title, subtitle, and statistics'

const statisticsSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Statistics').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or description",
    }),
    statistics: z.array(z.object({
        value: z.string().min(1).max(20).meta({
            description: "Statistical value (e.g., '250%', '$1.2M', '99.9%')",
        }),
        label: z.string().min(3).max(100).meta({
            description: "Description of the statistic",
        }),
        trend: z.enum(['up', 'down', 'neutral']).optional().meta({
            description: "Trend direction indicator",
        }),
        context: z.string().min(5).max(200).optional().meta({
            description: "Additional context or time period",
        })
    })).min(2).max(6).default([
        {
            value: '250%',
            label: 'Revenue Growth',
            trend: 'up',
            context: 'Year over year increase'
        },
        {
            value: '50M+',
            label: 'Active Users',
            trend: 'up',
            context: 'Global user base'
        },
        {
            value: '99.9%',
            label: 'Uptime',
            trend: 'neutral',
            context: 'Service reliability'
        },
        {
            value: '24/7',
            label: 'Support',
            trend: 'neutral',
            context: 'Customer service'
        }
    ]).describe('List of statistics (2-6 items)'),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = statisticsSlideSchema

export type StatisticsSlideData = z.infer<typeof statisticsSlideSchema>

interface StatisticsSlideLayoutProps {
    data?: Partial<StatisticsSlideData>
}

const StatisticsSlideLayout: React.FC<StatisticsSlideLayoutProps> = ({ data: slideData }) => {

    const statsCount = slideData?.statistics?.length || 0

    const getTrendIcon = (trend?: string) => {
        switch (trend) {
            case 'up':
                return '↗'
            case 'down':
                return '↘'
            case 'neutral':
                return '→'
            default:
                return ''
        }
    }

    const getGridCols = () => {
        if (statsCount <= 2) return 'grid-cols-2'
        if (statsCount <= 3) return 'grid-cols-3'
        if (statsCount <= 4) return 'grid-cols-2 lg:grid-cols-4'
        return 'grid-cols-2 lg:grid-cols-3'
    }

    return (
        <div
            className="relative w-full aspect-[16/9] bg-white overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300"
            style={slideData?.backgroundImage ? {
                backgroundImage: `url("${slideData.backgroundImage.__image_url__}")`,
                backgroundSize: 'cover',
                backgroundPosition: 'center',
                backgroundRepeat: 'no-repeat'
            } : {}}
        >
            {/* Enhanced geometric background decoration */}
            <div className="absolute inset-0 opacity-[0.03]">
                <div className="absolute top-0 right-0 w-96 h-96 bg-blue-600 rounded-full transform translate-x-32 -translate-y-32 blur-3xl" />
                <div className="absolute bottom-0 left-0 w-64 h-64 bg-blue-600 rounded-full transform -translate-x-16 translate-y-16 blur-2xl" />
            </div>

            <div className="relative z-10 flex flex-col h-full px-8 py-8">
                {/* Professional Header */}
                <header className="mb-6">
                    <h1 className={`text-4xl md:text-5xl font-bold mb-3 tracking-tight leading-tight break-words ${slideData?.backgroundImage
                        ? 'text-white drop-shadow-lg'
                        : 'text-slate-900'
                        }`}>
                        <span className="bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent">
                            {slideData?.title}
                        </span>
                    </h1>

                    {slideData?.subtitle && (
                        <p className={`text-xl font-light leading-relaxed break-words ${slideData?.backgroundImage
                            ? 'text-slate-200 drop-shadow-md'
                            : 'text-slate-600'
                            }`}>
                            {slideData?.subtitle}
                        </p>
                    )}

                    <div className="relative mt-4">
                        <div className="w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full shadow-lg" />
                        <div className="absolute inset-0 w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full blur-sm opacity-50" />
                    </div>
                </header>

                {/* Enhanced Statistics Grid */}
                <main className="flex-1 flex items-center justify-center">
                    <div className={`grid ${getGridCols()} gap-6 w-full max-w-6xl`}>
                        {slideData?.statistics?.map((stat, index) => (
                            <div key={index} className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 text-center relative overflow-hidden group hover:transform hover:scale-105 transition-all duration-300">
                                {/* Card accent */}
                                <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800" />

                                {/* Statistic Value */}
                                <div className="text-4xl md:text-5xl font-black mb-2 bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent">
                                    {stat?.value}
                                </div>

                                {/* Statistic Label */}
                                <h3 className="text-lg font-bold text-slate-900 mb-3 break-words leading-tight">
                                    {stat?.label}
                                </h3>

                                {/* Description */}
                                {stat?.context && (
                                    <p className="text-sm text-slate-600 leading-relaxed break-words mb-4">
                                        {stat?.context}
                                    </p>
                                )}

                                {/* Trend Indicator */}
                                {stat?.trend && (
                                    <div className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold border ${stat?.trend === 'up' ? 'text-emerald-600 bg-emerald-50 border-emerald-200' :
                                        stat?.trend === 'down' ? 'text-red-600 bg-red-50 border-red-200' :
                                            'text-slate-600 bg-slate-50 border-slate-200'
                                        }`}>
                                        <span className="mr-1">{getTrendIcon(stat?.trend)}</span>
                                        {stat?.trend === 'up' ? 'Trending Up' :
                                            stat?.trend === 'down' ? 'Trending Down' : 'Stable'}
                                    </div>
                                )}

                                {/* Background decoration */}
                                <div className="absolute bottom-0 right-0 w-16 h-16 bg-gradient-to-tl from-blue-600 to-blue-800 opacity-5 rounded-tl-full" />
                            </div>
                        ))}
                    </div>
                </main>
            </div>

            {/* Enhanced decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r from-blue-600 to-blue-800 shadow-lg">
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent" />
                <div className="absolute inset-0 bg-gradient-to-r from-blue-600 to-blue-800 blur-sm opacity-50" />
            </div>

            {/* Professional corner accents */}
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-blue-600 to-blue-800 opacity-5 rounded-bl-full" />
        </div>
    )
}

export default StatisticsSlideLayout 