import React from 'react'
import * as z from "zod";
import { ImageSchema } from './defaultSchemes';


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
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = quoteSlideSchema

export type QuoteSlideData = z.infer<typeof quoteSlideSchema>

interface QuoteSlideLayoutProps {
    data?: Partial<QuoteSlideData>
}

const QuoteSlideLayout: React.FC<QuoteSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData?.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >


            {/* Content */}
            <div className="relative z-10 h-full flex flex-col justify-center items-center px-16 py-12">
                {/* Title */}
                <h1 className="text-5xl font-black mb-16 leading-tight tracking-tight text-center max-w-4xl">
                    <span className="text-gray-900">{slideData?.title?.split(' ').slice(0, -1).join(' ')}</span>{' '}
                    <span className={`bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent`}>
                        {slideData?.title?.split(' ').slice(-1)[0]}
                    </span>
                </h1>

                {/* Quote */}
                <div className="max-w-4xl mx-auto text-center mb-16">
                    {/* Decorative line */}
                    <div className="relative flex justify-center mb-12">
                        <div className={`w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full shadow-lg`} />
                        <div className={`absolute inset-0 w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full blur-sm opacity-50`} />
                    </div>

                    {/* Quote Text */}
                    <blockquote className={`text-2xl md:text-3xl leading-relaxed mb-8 italic break-words relative z-10 font-light text-slate-700`}>
                        "{slideData?.quote}"
                    </blockquote>

                    {/* Author Attribution */}
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
                                <div className={`w-16 h-16 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold text-xl shadow-xl border-4 border-white`}>
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
                                <p className={`text-base font-semibold bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent break-words`}>
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

                    {/* Quote Accent Line */}
                    <div className="flex justify-center mt-6">
                        <div className="relative">
                            <div className={`w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full shadow-lg`} />
                            <div className={`absolute inset-0 w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full blur-sm opacity-50`} />
                        </div>
                    </div>

                    {/* Background decoration */}
                    <div className={`absolute bottom-0 right-0 w-20 h-20 bg-gradient-to-tl from-blue-600 to-blue-800 opacity-5 rounded-tl-full`} />
                </div>
            </div>

            {/* Decorative accent */}
            <div className={`absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r from-blue-600 to-blue-800 shadow-lg`}>
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent" />
                <div className={`absolute inset-0 bg-gradient-to-r from-blue-600 to-blue-800 blur-sm opacity-50`} />
            </div>

            {/* Professional corner accents */}
            <div className={`absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-blue-600 to-blue-800 opacity-5 rounded-bl-full`} />
        </div>
    )
}

export default QuoteSlideLayout 