import React from 'react'
import * as z from "zod";
import { ImageSchema } from './defaultSchemes';


export const layoutId = 'first-slide'
export const layoutName = 'First Slide'
export const layoutDescription = 'A slide with a title, subtitle, author, date, company, and background image'

const firstSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Welcome to Our Presentation').meta({
        description: "Main title of the presentation",
    }),
    subtitle: z.string().min(10).max(200).default('Subtitle for the slide').optional().meta({
        description: "Optional subtitle or tagline",
    }),
    author: z.string().max(100).default('John Doe').optional().meta({
        description: "Author or presenter name",
    }),
    date: z.string().optional().meta({
        description: "Presentation date",
    }),
    company: z.string().max(100).default('Company Name').optional().meta({
        description: "Company or organization name",
    }),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = firstSlideSchema

export type FirstSlideData = z.infer<typeof firstSlideSchema>

interface FirstSlideLayoutProps {
    data?: Partial<FirstSlideData>
}

const FirstSlideLayout: React.FC<FirstSlideLayoutProps> = ({ data: slideData }) => {


    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData.backgroundImage.__image_url__})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >


            {/* Content */}
            <div className="relative z-10 h-full flex flex-col">
                {/* Header */}
                <div className="flex-1 flex flex-col justify-center items-center text-center px-16 py-12">
                    {/* Title */}
                    <h1 className="text-7xl font-black mb-8 leading-tight tracking-tight max-w-4xl">
                        <span className="text-gray-900">{slideData?.title?.split(' ').slice(0, -1).join(' ')}</span>{' '}
                        <span className={`bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent`}>
                            {slideData?.title?.split(' ').slice(-1)[0]}
                        </span>
                    </h1>

                    {/* Subtitle */}
                    {slideData?.subtitle && (
                        <div className="relative mb-12">
                            <div className="relative">
                                <div className={`w-32 h-1.5 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full shadow-lg`} />
                                <div className={`absolute inset-0 w-32 h-1.5 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full blur-sm opacity-50`} />
                            </div>
                            <p className="text-2xl text-gray-600 font-light mt-6 max-w-2xl leading-relaxed">
                                {slideData.subtitle}
                            </p>
                        </div>
                    )}

                    {/* Date */}
                    {slideData?.date && (
                        <p className={`text-base font-medium break-words from-blue-600 to-blue-800 bg-gradient-to-r bg-clip-text text-transparent`}>
                            {slideData.date}
                        </p>
                    )}
                </div>

                {/* Footer with Author and Company */}
                <div className="px-16 pb-8">
                    <div className="flex justify-between items-end">
                        <div className="text-left">
                            {slideData?.author && (
                                <p className="text-lg font-semibold text-gray-800 mb-1">{slideData.author}</p>
                            )}
                            {slideData?.company && (
                                <p className="text-sm text-gray-600">{slideData.company}</p>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* Bottom accent */}
            <div className="absolute bottom-0 left-0 right-0">
                <div className={`absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r from-blue-600 to-blue-800 shadow-lg`}>
                    {/* Glow effect */}
                    <div className={`absolute inset-0 bg-gradient-to-r from-blue-600 to-blue-800 blur-sm opacity-50`} />
                </div>
                {/* Corner accents */}
                <div className={`absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-blue-600 to-blue-800 opacity-5 rounded-bl-full`} />
                <div className={`absolute bottom-0 left-0 w-24 h-24 bg-gradient-to-tr from-blue-600 to-blue-800 opacity-5 rounded-tr-full`} />
            </div>
        </div>
    )
}

export default FirstSlideLayout

