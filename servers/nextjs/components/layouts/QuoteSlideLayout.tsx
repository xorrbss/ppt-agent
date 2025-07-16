import React from 'react'
import * as z from "zod";


export const layoutId = 'quote-slide'
export const layoutName = 'Quote Slide'
export const layoutDescription = 'A slide with a title, subtitle, quote, author, author title, company, and author image'

const quoteSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Testimonials').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or description",
    }),
    quote: z.string().min(10).max(500).default('This solution has transformed our business operations and exceeded all expectations.').meta({
        description: "The main quote or testimonial",
    }),
    author: z.string().min(2).max(100).default('John Smith').meta({
        description: "Quote author name",
    }),
    authorTitle: z.string().min(2).max(100).optional().meta({
        description: "Author job title or position",
    }),
    company: z.string().min(2).max(100).optional().meta({
        description: "Author company or organization",
    }),
    authorImage: z.string().optional().meta({
        description: "URL to author photo",
    }),
    backgroundImage: z.string().optional().meta({
        description: "URL to background image for the slide",
    })
})

export const Schema = quoteSlideSchema

export type QuoteSlideData = z.infer<typeof quoteSlideSchema>

interface QuoteSlideLayoutProps {
    data?: Partial<QuoteSlideData>
    accentColor?: 'blue' | 'green' | 'purple' | 'orange' | 'red'
}

const QuoteSlideLayout: React.FC<QuoteSlideLayoutProps> = ({ data: slideData, accentColor = 'blue' }) => {

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

                {/* Enhanced Quote Content */}
                <main className="flex-1 flex items-center justify-center">
                    <div className="bg-white/95 backdrop-blur-lg rounded-3xl p-10 shadow-2xl border border-white/50 max-w-5xl text-center relative overflow-hidden">
                        {/* Enhanced Background Quote Decoration */}
                        <div className={`absolute top-4 left-6 text-8xl font-black opacity-10 bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent pointer-events-none select-none`}>
                            "
                        </div>
                        <div className={`absolute bottom-4 right-6 text-8xl font-black opacity-10 bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent pointer-events-none select-none rotate-180`}>
                            "
                        </div>

                        {/* Quote Text */}
                        <blockquote className={`text-2xl md:text-3xl leading-relaxed mb-8 italic break-words relative z-10 font-light ${slideData?.backgroundImage
                            ? 'text-slate-700'
                            : 'text-slate-700'
                            }`}>
                            "{slideData?.quote}"
                        </blockquote>

                        {/* Professional Author Attribution */}
                        <div className="flex items-center justify-center space-x-4 relative z-10">
                            {/* Author Avatar */}
                            <div className="flex-shrink-0">
                                {slideData?.authorImage ? (
                                    <img
                                        src={slideData?.authorImage}
                                        alt={slideData?.author}
                                        className="w-16 h-16 rounded-full object-cover shadow-xl border-4 border-white"
                                    />
                                ) : (
                                    <div className={`w-16 h-16 rounded-full ${accentSolids[accentColor]} flex items-center justify-center text-white font-bold text-xl shadow-xl border-4 border-white`}>
                                        {slideData?.author?.split(' ').map(n => n[0]).join('')}
                                    </div>
                                )}
                            </div>

                            {/* Author Details */}
                            <div className="text-left">
                                <p className="text-xl font-bold text-slate-900 break-words">
                                    {slideData?.author}
                                </p>

                                {slideData?.authorTitle && (
                                    <p className={`text-base font-semibold bg-gradient-to-r ${accentColors[accentColor]} bg-clip-text text-transparent break-words`}>
                                        {slideData?.authorTitle}
                                    </p>
                                )}

                                {slideData?.company && (
                                    <p className="text-sm text-slate-600 font-medium break-words">
                                        {slideData?.company}
                                    </p>
                                )}
                            </div>
                        </div>

                        {/* Enhanced Quote Accent Line */}
                        <div className="flex justify-center mt-6">
                            <div className="relative">
                                <div className={`w-24 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full shadow-lg`} />
                                <div className={`absolute inset-0 w-24 h-1 bg-gradient-to-r ${accentColors[accentColor]} rounded-full blur-sm opacity-50`} />
                            </div>
                        </div>

                        {/* Background decoration */}
                        <div className={`absolute bottom-0 right-0 w-20 h-20 bg-gradient-to-tl ${accentColors[accentColor]} opacity-5 rounded-tl-full`} />
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

export default QuoteSlideLayout 