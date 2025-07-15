import React from 'react'
import * as z from "zod";


export const layoutId = 'two-column-slide'
export const layoutName = 'Two Column Slide'
export const layoutDescription = 'A slide with a title, subtitle, and two columns of content'

const twoColumnSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Two Column Layout').describe('Title of the slide'),
    subtitle: z.string().min(3).max(150).optional().describe('Optional subtitle or description'),
    leftColumn: z.object({
        title: z.string().min(3).max(100).default('Left Column').describe('Left column title'),
        content: z.string().min(10).max(800).default('Content for the left column goes here. This can include detailed information, explanations, or supporting details.').describe('Left column content')
    }).default({
        title: 'Left Column',
        content: 'Content for the left column goes here. This can include detailed information, explanations, or supporting details.'
    }),
    rightColumn: z.object({
        title: z.string().min(3).max(100).default('Right Column').describe('Right column title'),
        content: z.string().min(10).max(800).default('Content for the right column goes here. This can include additional information, comparisons, or contrasting details.').describe('Right column content')
    }).default({
        title: 'Right Column',
        content: 'Content for the right column goes here. This can include additional information, comparisons, or contrasting details.'
    }),
    backgroundImage: z.string().optional().describe('URL to background image for the slide')
})

export const Schema = twoColumnSlideSchema


export type TwoColumnSlideData = z.infer<typeof twoColumnSlideSchema>

interface TwoColumnSlideLayoutProps {
    data?: Partial<TwoColumnSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const TwoColumnSlideLayout: React.FC<TwoColumnSlideLayoutProps> = ({ data, accentColor = 'blue' }) => {

    const slideData = twoColumnSlideSchema.parse(data || {})

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

                {/* Two Column Content with Enhanced Styling */}
                <main className="flex-1 grid grid-cols-2 gap-6">
                    {/* Left Column */}
                    <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-8 shadow-2xl border border-white/50 flex flex-col relative overflow-hidden hover:shadow-3xl transition-shadow duration-300">
                        {/* Column accent */}
                        <div className={`absolute top-0 left-0 w-full h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                        <h2 className={`text-2xl md:text-3xl font-bold mb-5 break-words ${slideData.backgroundImage
                            ? 'text-slate-900'
                            : 'text-slate-900'
                            }`}>
                            {slideData.leftColumn.title}
                        </h2>
                        <div className={`text-base md:text-lg leading-relaxed break-words flex-1 ${slideData.backgroundImage
                            ? 'text-slate-700'
                            : 'text-slate-700'
                            }`}>
                            {slideData.leftColumn.content.split('\n').map((paragraph, index) => (
                                paragraph.trim() && (
                                    <p key={index} className="mb-4 last:mb-0 font-medium">
                                        {paragraph}
                                    </p>
                                )
                            ))}
                        </div>
                    </div>

                    {/* Right Column */}
                    <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-8 shadow-2xl border border-white/50 flex flex-col relative overflow-hidden hover:shadow-3xl transition-shadow duration-300">
                        {/* Column accent */}
                        <div className={`absolute top-0 left-0 w-full h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                        <h2 className={`text-2xl md:text-3xl font-bold mb-5 break-words ${slideData.backgroundImage
                            ? 'text-slate-900'
                            : 'text-slate-900'
                            }`}>
                            {slideData.rightColumn.title}
                        </h2>
                        <div className={`text-base md:text-lg leading-relaxed break-words flex-1 ${slideData.backgroundImage
                            ? 'text-slate-700'
                            : 'text-slate-700'
                            }`}>
                            {slideData.rightColumn.content.split('\n').map((paragraph, index) => (
                                paragraph.trim() && (
                                    <p key={index} className="mb-4 last:mb-0 font-medium">
                                        {paragraph}
                                    </p>
                                )
                            ))}
                        </div>
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

export default TwoColumnSlideLayout 