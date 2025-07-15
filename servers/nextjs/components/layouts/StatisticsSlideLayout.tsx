import React from 'react'
import * as z from "zod";


export const layoutId = 'statistics-slide'
export const layoutName = 'Statistics Slide'
export const layoutDescription = 'A slide with a title, subtitle, and statistics'

const statisticsSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Statistics').describe('Title of the slide'),
    subtitle: z.string().min(3).max(150).optional().describe('Optional subtitle or description'),
    statistics: z.array(z.object({
        value: z.string().min(1).max(20).describe('Statistical value (e.g., "250%", "$1.2M", "99.9%")'),
        label: z.string().min(3).max(100).describe('Description of the statistic'),
        trend: z.enum(['up', 'down', 'neutral']).optional().describe('Trend direction indicator'),
        context: z.string().min(5).max(200).optional().describe('Additional context or time period')
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
    backgroundImage: z.string().optional().describe('URL to background image for the slide')
})

export const Schema = statisticsSlideSchema

export type StatisticsSlideData = z.infer<typeof statisticsSlideSchema>

interface StatisticsSlideLayoutProps {
    data?: Partial<StatisticsSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const StatisticsSlideLayout: React.FC<StatisticsSlideLayoutProps> = ({ data, accentColor = 'blue' }) => {
    const slideData = statisticsSlideSchema.parse(data || {})
    const statsCount = slideData.statistics.length

    const accentColors = {
        blue: 'from-blue-600 to-blue-800',
        green: 'from-emerald-600 to-emerald-800',
        purple: 'from-violet-600 to-violet-800',
        orange: 'from-orange-600 to-orange-800',
        red: 'from-red-600 to-red-800'
    }

    const accentSolids = {
        blue: 'bg-blue-600',
        green: 'bg-emerald-600',
        purple: 'bg-violet-600',
        orange: 'bg-orange-600',
        red: 'bg-red-600'
    }

    const trendColors = {
        up: {
            blue: 'text-emerald-600 bg-emerald-50 border-emerald-200',
            green: 'text-emerald-600 bg-emerald-50 border-emerald-200',
            purple: 'text-emerald-600 bg-emerald-50 border-emerald-200',
            orange: 'text-emerald-600 bg-emerald-50 border-emerald-200',
            red: 'text-emerald-600 bg-emerald-50 border-emerald-200'
        },
        down: 'text-red-600 bg-red-50 border-red-200',
        neutral: 'text-slate-600 bg-slate-50 border-slate-200'
    }

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
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200"
            style={slideData.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >
            {/* Enhanced geometric background decoration */}
            <div className="absolute inset-0 opacity-[0.03]">
                <div className={`absolute top-0 right-0 w-96 h-96 ${accentSolids[accentColor]} rounded-full transform translate-x-32 -translate-y-32 blur-3xl`} />
                <div className={`absolute bottom-0 left-0 w-64 h-64 ${accentSolids[accentColor]} rounded-full transform -translate-x-16 translate-y-16 blur-2xl`} />
            </div>

            <div className="relative z-10 flex flex-col h-full px-8 py-8">
                {/* Professional Header */}
                <header className="mb-6">
                    <h1 className={`text-4xl md:text-5xl font-bold mb-3 tracking-tight leading-tight break-words ${slideData.backgroundImage
                        ? 'text-white drop-shadow-lg'
                        : 'text-slate-900'
                        }`}>
                        <span className={`bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent`}>
                            {slideData.title}
                        </span>
                    </h1>

                    {slideData.subtitle && (
                        <p className={`text-xl font-light leading-relaxed break-words ${slideData.backgroundImage
                            ? 'text-slate-200 drop-shadow-md'
                            : 'text-slate-600'
                            }`}>
                            {slideData.subtitle}
                        </p>
                    )}

                    <div className="relative mt-4">
                        <div className={`w-32 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full shadow-lg`} />
                        <div className={`absolute inset-0 w-32 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full blur-sm opacity-50`} />
                    </div>
                </header>

                {/* Enhanced Statistics Grid */}
                <main className="flex-1 flex items-center justify-center">
                    <div className={`grid ${getGridCols()} gap-6 w-full max-w-6xl`}>
                        {slideData.statistics.map((stat, index) => (
                            <div key={index} className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 text-center relative overflow-hidden group hover:transform hover:scale-105 transition-all duration-300">
                                {/* Card accent */}
                                <div className={`absolute top-0 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                                {/* Statistic Value */}
                                <div className={`text-4xl md:text-5xl font-black mb-2 bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent`}>
                                    {stat.value}
                                </div>

                                {/* Statistic Label */}
                                <h3 className="text-lg md:text-xl font-bold text-slate-900 mb-2 break-words">
                                    {stat.label}
                                </h3>

                                {/* Trend Indicator */}
                                {stat.trend && (
                                    <div className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold border ${stat.trend === 'up' ? trendColors.up[accentColor] :
                                        stat.trend === 'down' ? trendColors.down :
                                            trendColors.neutral
                                        } mb-2`}>
                                        <span className="mr-1">{getTrendIcon(stat.trend)}</span>
                                        {stat.trend.toUpperCase()}
                                    </div>
                                )}

                                {/* Context */}
                                {stat.context && (
                                    <p className="text-sm text-slate-600 break-words font-medium">
                                        {stat.context}
                                    </p>
                                )}

                                {/* Background decoration */}
                                <div className={`absolute bottom-0 right-0 w-16 h-16 bg-gradient-to-tl ${accentColors[accentColor]} opacity-5 rounded-tl-full`} />
                            </div>
                        ))}
                    </div>
                </main>
            </div>

            {/* Enhanced decorative accent */}
            <div className={`absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r ${accentColors[accentColor]} shadow-lg`}>
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent" />
                <div className={`absolute inset-0 bg-gradient-to-r ${accentColors[accentColor]} blur-sm opacity-50`} />
            </div>

            {/* Professional corner accents */}
            <div className={`absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl ${accentColors[accentColor]} opacity-5 rounded-bl-full`} />
        </div>
    )
}

export default StatisticsSlideLayout 