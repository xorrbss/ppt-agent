import React from 'react'
import * as z from "zod";
import { imageSchema } from './defaultSchemes';


export const layoutId = 'process-slide'
export const layoutName = 'Process Slide'
export const layoutDescription = 'A slide with a title, subtitle, and process steps'

const processSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Our Process').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or description",
    }),
    processSteps: z.array(z.object({
        step: z.number().min(1).max(10).meta({
            description: "Step number",
        }),
        title: z.string().min(3).max(100).meta({
            description: "Step title",
        }),
        description: z.string().min(10).max(200).meta({
            description: "Step description",
        })
    })).min(2).max(6).default([
        {
            step: 1,
            title: 'Discovery',
            description: 'Understanding requirements and gathering initial insights'
        },
        {
            step: 2,
            title: 'Planning',
            description: 'Strategic planning and roadmap development'
        },
        {
            step: 3,
            title: 'Implementation',
            description: 'Executing the plan with precision and quality'
        },
        {
            step: 4,
            title: 'Delivery',
            description: 'Final delivery and ongoing support'
        }
    ]).describe('Process steps (2-6 items)'),
    backgroundImage: imageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = processSlideSchema

export type ProcessSlideData = z.infer<typeof processSlideSchema>

interface ProcessSlideLayoutProps {
    data?: Partial<ProcessSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const ProcessSlideLayout: React.FC<ProcessSlideLayoutProps> = ({ data: slideData, accentColor = 'blue' }) => {

    const accentColors = {
        blue: 'from-blue-600 to-blue-800',
        green: 'from-emerald-600 to-emerald-800',
        purple: 'from-violet-600 to-violet-800',
        orange: 'from-orange-600 to-orange-800',
        red: 'from-red-600 to-red-800'
    }

    const stepColors = {
        blue: 'bg-blue-600 text-white border-blue-600',
        green: 'bg-emerald-600 text-white border-emerald-600',
        purple: 'bg-violet-600 text-white border-violet-600',
        orange: 'bg-orange-600 text-white border-orange-600',
        red: 'bg-red-600 text-white border-red-600'
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
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData?.backgroundImage})`,
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
                    <h1 className={`text-4xl md:text-5xl font-bold mb-3 tracking-tight leading-tight break-words ${slideData?.backgroundImage
                        ? 'text-white drop-shadow-lg'
                        : 'text-slate-900'
                        }`}>
                        <span className={`bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent`}>
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
                        <div className={`w-32 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full shadow-lg`} />
                        <div className={`absolute inset-0 w-32 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full blur-sm opacity-50`} />
                    </div>
                </header>

                {/* Enhanced Process Steps */}
                <main className="flex-1 flex items-center justify-center">
                    <div className="w-full max-w-6xl">
                        <div className="flex items-center justify-between">
                            {slideData?.processSteps?.map((step, index) => (
                                <React.Fragment key={index}>
                                    {/* Process Step */}
                                    <div className="flex flex-col items-center text-center group" style={{ width: `${100 / (slideData?.processSteps?.length || 0)}%` }}>
                                        {/* Step Number Circle */}
                                        <div className={`w-16 h-16 rounded-full ${stepColors[accentColor]} flex items-center justify-center text-2xl font-bold mb-4 shadow-2xl border-4 group-hover:scale-110 transition-all duration-300 relative`}>
                                            <span className="relative z-10">{step.step}</span>
                                            <div className={`absolute inset-0 rounded-full ${accentSolids[accentColor]} blur-md opacity-50`} />
                                        </div>

                                        {/* Step Content Card */}
                                        <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 w-full max-w-xs relative overflow-hidden group-hover:transform group-hover:scale-105 transition-all duration-300">
                                            {/* Card accent */}
                                            <div className={`absolute top-0 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                                            {/* Step Title */}
                                            <h3 className="text-xl font-bold text-slate-900 mb-3 break-words">
                                                {step.title}
                                            </h3>

                                            {/* Step Description */}
                                            <p className="text-sm text-slate-600 leading-relaxed break-words font-medium">
                                                {step.description}
                                            </p>

                                            {/* Background decoration */}
                                            <div className={`absolute bottom-0 right-0 w-12 h-12 bg-gradient-to-tl ${accentColors[accentColor]} opacity-5 rounded-tl-full`} />
                                        </div>
                                    </div>

                                    {/* Arrow Between Steps */}
                                    {index < (slideData?.processSteps?.length || 0) - 1 && (
                                        <div className="flex items-center justify-center mx-4">
                                            <div className={`w-8 h-1 bg-gradient-to-r ${accentColors[accentColor]} relative`}>
                                                <div className={`absolute -right-2 -top-1 w-0 h-0 border-l-4 border-t-2 border-b-2 ${accentSolids[accentColor]} border-t-transparent border-b-transparent`}
                                                    style={{
                                                        borderLeftColor: accentColor === 'blue' ? '#2563eb' :
                                                            accentColor === 'green' ? '#059669' :
                                                                accentColor === 'purple' ? '#7c3aed' :
                                                                    accentColor === 'orange' ? '#ea580c' : '#dc2626'
                                                    }} />
                                            </div>
                                        </div>
                                    )}
                                </React.Fragment>
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

export default ProcessSlideLayout 