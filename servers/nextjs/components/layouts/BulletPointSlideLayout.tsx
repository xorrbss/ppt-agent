import React from 'react'
import { zodToJsonSchema } from "zod-to-json-schema";
import * as z from "zod";

export const layoutId = 'bullet-point-slide'
export const layoutName = 'Bullet Point Slide'
export const layoutDescription = 'A slide with a title, subtitle, and a list of bullet points.'

const bulletPointSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Points').describe('Title of the slide'),
    subtitle: z.string().min(3).max(150).optional().describe('Optional subtitle or description'),
    icon: z.string().optional().describe('Icon to display in the slide'),
    bulletPoints: z.array(z.string().min(5).max(200)).min(2).max(8).default([
        'First key point that highlights important information',
        'Second bullet point with valuable insights',
        'Third point demonstrating clear benefits',
        'Fourth item showcasing key features'
    ]).describe('List of bullet points (2-8 items)'),
    backgroundImage: z.string().optional().describe('URL to background image for the slide')
})


export const Schema = bulletPointSlideSchema

console.log(zodToJsonSchema(Schema, {
    removeAdditionalStrategy: 'strict',
}))

export type BulletPointSlideData = z.infer<typeof bulletPointSlideSchema>

interface BulletPointSlideLayoutProps {
    data?: Partial<BulletPointSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const BulletPointSlideLayout: React.FC<BulletPointSlideLayoutProps> = ({ data, accentColor = 'blue' }) => {
    const slideData = bulletPointSlideSchema.parse(data || {})

    const accentColors = {
        blue: 'from-blue-600 to-blue-800',
        green: 'from-emerald-600 to-emerald-800',
        purple: 'from-violet-600 to-violet-800',
        orange: 'from-orange-600 to-orange-800',
        red: 'from-red-600 to-red-800'
    }

    const bulletColors = {
        blue: 'bg-blue-600',
        green: 'bg-emerald-600',
        purple: 'bg-violet-600',
        orange: 'bg-orange-600',
        red: 'bg-red-600'
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

                {/* Enhanced Bullet Points */}
                <main className="flex-1 flex items-center justify-center">
                    <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-8 shadow-2xl border border-white/50 w-full max-w-5xl relative overflow-hidden">
                        {/* Content background accent */}
                        <div className={`absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl ${accentColors[accentColor]} opacity-5 rounded-bl-full`} />

                        <ul className={`space-y-${slideData.bulletPoints.length <= 4 ? '6' : slideData.bulletPoints.length <= 6 ? '4' : '3'} relative z-10`}>
                            {slideData.bulletPoints.map((point, index) => (
                                <li key={index} className="flex items-start group hover:transform hover:translateX-2 transition-all duration-200">
                                    {/* Enhanced bullet point icon */}
                                    <div className="relative mr-6 mt-1 flex-shrink-0">
                                        <div className={`${bulletColors[accentColor]} w-4 h-4 rounded-full shadow-lg group-hover:scale-125 transition-all duration-200 relative z-10`} />
                                        <div className={`absolute inset-0 ${bulletColors[accentColor]} w-4 h-4 rounded-full blur-sm opacity-50 group-hover:opacity-75 transition-opacity duration-200`} />
                                    </div>

                                    {/* Enhanced bullet text */}
                                    <span className={`text-lg md:text-xl leading-relaxed break-words font-medium ${slideData.backgroundImage
                                        ? 'text-slate-700'
                                        : 'text-slate-700'
                                        } group-hover:text-slate-900 transition-colors duration-200`}>
                                        {point}
                                    </span>
                                </li>
                            ))}
                        </ul>
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

export default BulletPointSlideLayout 