import React from 'react'
import * as z from "zod";

export const layoutId = 'conclusion-slide'
export const layoutName = 'Conclusion Slide'
export const layoutDescription = 'A slide with a title, subtitle, key takeaways, call to action, and contact information'

const conclusionSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Conclusion').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or description",
    }),
    keyTakeaways: z.array(z.string().min(5).max(200)).min(2).max(6).default([
        'Successfully achieved our primary objectives',
        'Demonstrated significant value and impact',
        'Established clear next steps for continued success',
        'Built strong foundation for future growth'
    ]).meta({
        description: "Key takeaways or summary points (2-6 items)",
    }),
    callToAction: z.string().min(5).max(150).optional().meta({
        description: "Optional call to action or next steps",
    }),
    contactInfo: z.object({
        email: z.string().email().optional().meta({
            description: "Contact email",
        }),
        phone: z.string().min(5).max(50).optional().meta({
            description: "Contact phone number",
        }),
        website: z.string().url().optional().meta({
            description: "Website URL",
        })
    }).optional().meta({
        description: "Optional contact information",
    }),
    backgroundImage: z.string().optional().meta({
        description: "URL to background image for the slide",
    })
})

export const Schema = conclusionSlideSchema

export type ConclusionSlideData = z.infer<typeof conclusionSlideSchema>

interface ConclusionSlideLayoutProps {
    data?: Partial<ConclusionSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const ConclusionSlideLayout: React.FC<ConclusionSlideLayoutProps> = ({ data, accentColor = 'blue' }) => {
    const slideData = conclusionSlideSchema.parse(data || {})

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

                {/* Enhanced Content Layout */}
                <main className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-8">
                    {/* Key Takeaways - Takes up 2/3 of space */}
                    <div className="lg:col-span-2">
                        <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-8 shadow-2xl border border-white/50 h-full relative overflow-hidden">
                            {/* Content accent */}
                            <div className={`absolute top-0 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                            <h2 className="text-2xl font-bold text-slate-900 mb-6 relative z-10">Key Takeaways</h2>

                            <ul className={`space-y-4 relative z-10`}>
                                {slideData.keyTakeaways.map((takeaway, index) => (
                                    <li key={index} className="flex items-start group hover:transform hover:translateX-2 transition-all duration-200">
                                        {/* Enhanced bullet point */}
                                        <div className="relative mr-4 mt-1.5 flex-shrink-0">
                                            <div className={`${bulletColors[accentColor]} w-3 h-3 rounded-full shadow-lg group-hover:scale-125 transition-all duration-200 relative z-10`} />
                                            <div className={`absolute inset-0 ${bulletColors[accentColor]} w-3 h-3 rounded-full blur-sm opacity-50 group-hover:opacity-75 transition-opacity duration-200`} />
                                        </div>

                                        <span className="text-lg leading-relaxed break-words font-medium text-slate-700 group-hover:text-slate-900 transition-colors duration-200">
                                            {takeaway}
                                        </span>
                                    </li>
                                ))}
                            </ul>

                            {/* Background decoration */}
                            <div className={`absolute bottom-0 right-0 w-16 h-16 bg-gradient-to-tl ${accentColors[accentColor]} opacity-5 rounded-tl-full`} />
                        </div>
                    </div>

                    {/* Call to Action & Contact Info - Takes up 1/3 of space */}
                    <div className="space-y-6">
                        {/* Call to Action */}
                        {slideData.callToAction && (
                            <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 text-center relative overflow-hidden">
                                {/* CTA accent */}
                                <div className={`absolute top-0 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                                <div className={`w-12 h-12 mx-auto mb-4 rounded-full ${accentSolids[accentColor]} flex items-center justify-center shadow-lg relative`}>
                                    <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
                                    </svg>
                                    <div className={`absolute inset-0 rounded-full ${accentSolids[accentColor]} blur-md opacity-50`} />
                                </div>

                                <h3 className="text-lg font-bold text-slate-900 mb-3">Next Steps</h3>
                                <p className="text-sm text-slate-600 leading-relaxed break-words font-medium">
                                    {slideData.callToAction}
                                </p>
                            </div>
                        )}

                        {/* Contact Information */}
                        {slideData.contactInfo && Object.values(slideData.contactInfo).some(Boolean) && (
                            <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 relative overflow-hidden">
                                {/* Contact accent */}
                                <div className={`absolute top-0 left-0 right-0 h-1 bg-gradient-to-r ${accentColors[accentColor]}`} />

                                <h3 className="text-lg font-bold text-slate-900 mb-4 text-center">Get in Touch</h3>

                                <div className="space-y-3">
                                    {slideData.contactInfo.email && (
                                        <a
                                            href={`mailto:${slideData.contactInfo.email}`}
                                            className="flex items-center space-x-3 p-3 rounded-xl bg-slate-50 hover:bg-slate-100 transition-colors duration-200 group"
                                        >
                                            <div className={`w-8 h-8 rounded-full ${accentSolids[accentColor]} flex items-center justify-center group-hover:scale-110 transition-transform duration-200`}>
                                                <svg className="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 20 20">
                                                    <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z" />
                                                    <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z" />
                                                </svg>
                                            </div>
                                            <span className="text-sm font-medium text-slate-700 break-all">{slideData.contactInfo.email}</span>
                                        </a>
                                    )}

                                    {slideData.contactInfo.phone && (
                                        <a
                                            href={`tel:${slideData.contactInfo.phone}`}
                                            className="flex items-center space-x-3 p-3 rounded-xl bg-slate-50 hover:bg-slate-100 transition-colors duration-200 group"
                                        >
                                            <div className={`w-8 h-8 rounded-full ${accentSolids[accentColor]} flex items-center justify-center group-hover:scale-110 transition-transform duration-200`}>
                                                <svg className="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 20 20">
                                                    <path d="M2 3a1 1 0 011-1h2.153a1 1 0 01.986.836l.74 4.435a1 1 0 01-.54 1.06l-1.548.773a11.037 11.037 0 006.105 6.105l.774-1.548a1 1 0 011.059-.54l4.435.74a1 1 0 01.836.986V17a1 1 0 01-1 1h-2C7.82 18 2 12.18 2 5V3z" />
                                                </svg>
                                            </div>
                                            <span className="text-sm font-medium text-slate-700">{slideData.contactInfo.phone}</span>
                                        </a>
                                    )}

                                    {slideData.contactInfo.website && (
                                        <a
                                            href={slideData.contactInfo.website}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="flex items-center space-x-3 p-3 rounded-xl bg-slate-50 hover:bg-slate-100 transition-colors duration-200 group"
                                        >
                                            <div className={`w-8 h-8 rounded-full ${accentSolids[accentColor]} flex items-center justify-center group-hover:scale-110 transition-transform duration-200`}>
                                                <svg className="w-4 h-4 text-white" fill="currentColor" viewBox="0 0 20 20">
                                                    <path fillRule="evenodd" d="M4.083 9h1.946c.089-1.546.383-2.97.837-4.118A6.004 6.004 0 004.083 9zM10 2a8 8 0 100 16 8 8 0 000-16zm0 2c-.076 0-.232.032-.465.262-.238.234-.497.623-.737 1.182-.389.907-.673 2.142-.766 3.556h3.936c-.093-1.414-.377-2.649-.766-3.556-.24-.559-.5-.948-.737-1.182C10.232 4.032 10.076 4 10 4zm3.971 5c-.089-1.546-.383-2.97-.837-4.118A6.004 6.004 0 0115.917 9h-1.946zm-2.003 2H8.032c.093 1.414.377 2.649.766 3.556.24.559.5.948.737 1.182.233.23.389.262.465.262.076 0 .232-.032.465-.262.238-.234.498-.623.737-1.182.389-.907.673-2.142.766-3.556zm1.166 4.118c.454-1.147.748-2.572.837-4.118h1.946a6.004 6.004 0 01-2.783 4.118zm-6.268 0C6.412 13.97 6.118 12.546 6.03 11H4.083a6.004 6.004 0 002.783 4.118z" clipRule="evenodd" />
                                                </svg>
                                            </div>
                                            <span className="text-sm font-medium text-slate-700 break-all">{slideData.contactInfo.website.replace(/^https?:\/\//, '')}</span>
                                        </a>
                                    )}
                                </div>

                                {/* Background decoration */}
                                <div className={`absolute bottom-0 right-0 w-12 h-12 bg-gradient-to-tl ${accentColors[accentColor]} opacity-5 rounded-tl-full`} />
                            </div>
                        )}
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

export default ConclusionSlideLayout 