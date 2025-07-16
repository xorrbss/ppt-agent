import React from 'react'
import * as z from "zod";
import { imageSchema } from './defaultSchemes';


export const layoutId = 'timeline-slide'
export const layoutName = 'Timeline Slide'
export const layoutDescription = 'A slide with a title, subtitle, and timeline items'

const timelineSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Project Timeline').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or description",
    }),
    timelineItems: z.array(z.object({
        date: z.string().min(2).max(50).meta({
            description: "Date or time period",
        }),
        title: z.string().min(3).max(100).meta({
            description: "Event or milestone title",
        }),
        description: z.string().min(10).max(300).meta({
            description: "Event description",
        }),
        status: z.enum(['completed', 'current', 'upcoming']).default('upcoming').meta({
            description: "Timeline item status",
        })
    })).min(2).max(6).default([
        {
            date: 'Q1 2024',
            title: 'Project Initiation',
            description: 'Project planning, team assembly, and initial requirements gathering',
            status: 'completed'
        },
        {
            date: 'Q2 2024',
            title: 'Development Phase',
            description: 'Core development work, prototype creation, and testing implementation',
            status: 'current'
        },
        {
            date: 'Q3 2024',
            title: 'Testing & QA',
            description: 'Comprehensive testing, quality assurance, and user acceptance testing',
            status: 'upcoming'
        },
        {
            date: 'Q4 2024',
            title: 'Launch & Deployment',
            description: 'Final deployment, go-live activities, and post-launch monitoring',
            status: 'upcoming'
        }
    ]).meta({
        description: "Timeline events (2-6 items)",
    }),
    backgroundImage: imageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = timelineSlideSchema

export type TimelineSlideData = z.infer<typeof timelineSlideSchema>

interface TimelineSlideLayoutProps {
    data?: Partial<TimelineSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const TimelineSlideLayout: React.FC<TimelineSlideLayoutProps> = ({ data: slideData, accentColor = 'blue' }) => {

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

    const statusColors = {
        completed: {
            blue: 'bg-emerald-600 border-emerald-600 shadow-emerald-200',
            green: 'bg-emerald-600 border-emerald-600 shadow-emerald-200',
            purple: 'bg-emerald-600 border-emerald-600 shadow-emerald-200',
            orange: 'bg-emerald-600 border-emerald-600 shadow-emerald-200',
            red: 'bg-emerald-600 border-emerald-600 shadow-emerald-200'
        },
        current: {
            blue: 'bg-blue-600 border-blue-600 ring-4 ring-blue-200 shadow-blue-300',
            green: 'bg-emerald-600 border-emerald-600 ring-4 ring-emerald-200 shadow-emerald-300',
            purple: 'bg-violet-600 border-violet-600 ring-4 ring-violet-200 shadow-violet-300',
            orange: 'bg-orange-600 border-orange-600 ring-4 ring-orange-200 shadow-orange-300',
            red: 'bg-red-600 border-red-600 ring-4 ring-red-200 shadow-red-300'
        },
        upcoming: {
            blue: 'bg-slate-300 border-slate-400 shadow-slate-200',
            green: 'bg-slate-300 border-slate-400 shadow-slate-200',
            purple: 'bg-slate-300 border-slate-400 shadow-slate-200',
            orange: 'bg-slate-300 border-slate-400 shadow-slate-200',
            red: 'bg-slate-300 border-slate-400 shadow-slate-200'
        }
    }

    const lineColors = {
        blue: 'bg-blue-200',
        green: 'bg-emerald-200',
        purple: 'bg-violet-200',
        orange: 'bg-orange-200',
        red: 'bg-red-200'
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

                {/* Enhanced Timeline */}
                <main className="flex-1 flex items-center justify-center">
                    <div className="w-full max-w-6xl">
                        <div className="relative flex justify-between items-start">
                            {/* Timeline line */}
                            <div className={`absolute top-8 left-0 right-0 h-1 ${lineColors[accentColor]} rounded-full`} />
                            <div className={`absolute top-8 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full opacity-20`} />

                            {slideData?.timelineItems?.map((item, index) => (
                                <div key={index} className="relative flex flex-col items-center" style={{ width: `${100 / (slideData?.timelineItems?.length || 0)}%` }}>
                                    {/* Timeline node */}
                                    <div className={`w-6 h-6 rounded-full border-4 shadow-lg ${statusColors[item.status][accentColor]} relative z-10 mb-4 transition-all duration-300 hover:scale-110`}>
                                        {item.status === 'completed' && (
                                            <div className="absolute inset-0 flex items-center justify-center">
                                                <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                                                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                                                </svg>
                                            </div>
                                        )}
                                        {item.status === 'current' && (
                                            <div className="absolute inset-0 flex items-center justify-center">
                                                <div className="w-2 h-2 bg-white rounded-full animate-pulse" />
                                            </div>
                                        )}
                                    </div>

                                    {/* Timeline content card */}
                                    <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 text-center min-h-[180px] flex flex-col justify-between relative overflow-hidden group hover:transform hover:scale-105 transition-all duration-300">
                                        {/* Card accent */}
                                        <div className={`absolute top-0 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                                        <div>
                                            {/* Date */}
                                            <div className={`text-sm font-bold mb-2 px-3 py-1 rounded-full bg-gradient-to-r ${accentColors[accentColor]} text-white inline-block`}>
                                                {item.date}
                                            </div>

                                            {/* Title */}
                                            <h3 className="text-lg font-bold text-slate-900 mb-3 break-words">
                                                {item.title}
                                            </h3>

                                            {/* Description */}
                                            <p className="text-sm text-slate-600 leading-relaxed break-words font-medium">
                                                {item.description}
                                            </p>
                                        </div>

                                        {/* Status indicator */}
                                        <div className="mt-4">
                                            <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold ${item.status === 'completed' ? 'bg-emerald-100 text-emerald-800' :
                                                item.status === 'current' ? `${accentColors[accentColor]} bg-opacity-10 text-${accentColor}-800` :
                                                    'bg-slate-100 text-slate-600'
                                                }`}>
                                                {item.status === 'completed' && '✓ Completed'}
                                                {item.status === 'current' && '● In Progress'}
                                                {item.status === 'upcoming' && '○ Upcoming'}
                                            </span>
                                        </div>

                                        {/* Background decoration */}
                                        <div className={`absolute bottom-0 right-0 w-12 h-12 bg-gradient-to-tl ${accentColors[accentColor]} opacity-5 rounded-tl-full`} />
                                    </div>
                                </div>
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

export default TimelineSlideLayout 