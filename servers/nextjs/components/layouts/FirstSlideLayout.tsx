import React from 'react'
import * as z from "zod";


export const layoutId = 'first-slide'
export const layoutName = 'First Slide'
export const layoutDescription = 'A slide with a title, subtitle, author, date, company, and background image'

const firstSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Welcome to Our Presentation').describe('Main title of the presentation'),
    subtitle: z.string().min(10).max(200).default('Subtitle for the slide').optional().describe('Optional subtitle or tagline'),
    author: z.string().min(2).max(100).default('John Doe').optional().describe('Author or presenter name'),
    date: z.string().optional().describe('Presentation date'),
    company: z.string().min(2).max(100).default('Company Name').optional().describe('Company or organization name'),
    backgroundImage: z.string().optional().describe('URL to background image for the slide')
})

export const Schema = firstSlideSchema

export type FirstSlideData = z.infer<typeof firstSlideSchema>

interface FirstSlideLayoutProps {
    data?: Partial<FirstSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const FirstSlideLayout: React.FC<FirstSlideLayoutProps> = ({ data, accentColor = 'blue' }) => {
    const slideData = firstSlideSchema.parse(data || {})

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
                <div className={`absolute top-1/2 left-1/2 w-48 h-48 ${accentSolids[accentColor]} rounded-full transform -translate-x-1/2 -translate-y-1/2 blur-3xl opacity-50`} />
            </div>

            {/* Grid overlay for professional look */}
            <div className="absolute inset-0 opacity-[0.02]" style={{
                backgroundImage: `linear-gradient(0deg, rgba(0,0,0,0.1) 1px, transparent 1px),
                                 linear-gradient(90deg, rgba(0,0,0,0.1) 1px, transparent 1px)`,
                backgroundSize: '60px 60px'
            }} />

            <div className="relative z-10 flex flex-col h-full px-8 py-8 justify-center items-center text-center">
                {/* Main Content */}
                <main className="flex-1 flex flex-col justify-center items-center max-w-5xl">
                    {/* Title */}
                    <h1 className={`text-5xl md:text-6xl font-black mb-6 tracking-tight leading-[0.9] break-words ${slideData.backgroundImage
                        ? 'text-white drop-shadow-2xl'
                        : 'text-slate-900'
                        }`}>
                        <span className={`bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent`}>
                            {slideData.title}
                        </span>
                    </h1>

                    {/* Subtitle */}
                    {slideData.subtitle && (
                        <p className={`text-xl md:text-2xl font-light leading-relaxed mb-8 break-words max-w-4xl ${slideData.backgroundImage
                            ? 'text-slate-200 drop-shadow-lg'
                            : 'text-slate-600'
                            }`}>
                            {slideData.subtitle}
                        </p>
                    )}

                    {/* Enhanced Accent Line */}
                    <div className="relative mb-8">
                        <div className={`w-32 h-1.5 bg-gradient-to-r ${accentColors[accentColor]} rounded-full shadow-lg`} />
                        <div className={`absolute inset-0 w-32 h-1.5 bg-gradient-to-r ${accentColors[accentColor]} rounded-full blur-sm opacity-50`} />
                    </div>

                    {/* Professional Metadata Container */}
                    <div className="bg-white/90 backdrop-blur-md rounded-2xl px-8 py-6 shadow-xl border border-white/40">
                        <div className="space-y-3">
                            {slideData.author && (
                                <p className={`text-lg font-semibold break-words text-slate-800`}>
                                    {slideData.author}
                                </p>
                            )}

                            {slideData.company && (
                                <p className={`text-base font-medium break-words ${accentColors[accentColor]} bg-gradient-to-r bg-clip-text text-transparent`}>
                                    {slideData.company}
                                </p>
                            )}

                            {slideData.date && (
                                <p className={`text-sm break-words text-slate-600 font-medium tracking-wide`}>
                                    {slideData.date}
                                </p>
                            )}
                        </div>
                    </div>
                </main>
            </div>

            {/* Enhanced decorative accent with glow effect */}
            <div className={`absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r ${accentColors[accentColor]} shadow-lg`}>
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent" />
                <div className={`absolute inset-0 bg-gradient-to-r ${accentColors[accentColor]} blur-sm opacity-50`} />
            </div>

            {/* Professional corner accents */}
            <div className={`absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl ${accentColors[accentColor]} opacity-5 rounded-bl-full`} />
            <div className={`absolute bottom-0 left-0 w-24 h-24 bg-gradient-to-tr ${accentColors[accentColor]} opacity-5 rounded-tr-full`} />
        </div>
    )
}

export default FirstSlideLayout

