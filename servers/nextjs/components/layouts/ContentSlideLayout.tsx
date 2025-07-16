import React from 'react'
import * as z from "zod";
import { ImageSchema } from './defaultSchemes';


export const layoutId = 'content-slide'
export const layoutName = 'Content Slide'
export const layoutDescription = 'A slide with a title, subtitle, and content'

const contentSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Slide Title').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or description",
    }),
    content: z.string().min(10).max(1000).default('Your slide content goes here. This is where you can add detailed information, explanations, or any other text content that supports your presentation.').meta({
        description: "Main content text",
    }),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = contentSlideSchema

export type ContentSlideData = z.infer<typeof contentSlideSchema>

interface ContentSlideLayoutProps {
    data?: Partial<ContentSlideData>
}

const ContentSlideLayout: React.FC<ContentSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData?.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >


            {/* Grid overlay for professional look */}
            <div className="absolute inset-0 opacity-[0.015]" style={{
                backgroundImage: `linear-gradient(0deg, rgba(0,0,0,0.1) 1px, transparent 1px),
                                 linear-gradient(90deg, rgba(0,0,0,0.1) 1px, transparent 1px)`,
                backgroundSize: '40px 40px'
            }} />

            {/* Content */}
            <div className="relative z-10 h-full flex flex-col px-16 py-12">
                {/* Header */}
                <div className="text-center mb-16">
                    <h1 className="text-6xl font-black mb-8 leading-tight tracking-tight max-w-4xl mx-auto">
                        <span className="text-gray-900">{slideData?.title?.split(' ').slice(0, -1).join(' ')}</span>{' '}
                        <span className={`bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent`}>
                            {slideData?.title?.split(' ').slice(-1)[0]}
                        </span>
                    </h1>

                    {/* Subtitle */}
                    {slideData?.subtitle && (
                        <div className="relative mb-8">
                            <div className="relative flex justify-center">
                                <div className={`w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full shadow-lg`} />
                                <div className={`absolute inset-0 w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full blur-sm opacity-50`} />
                            </div>
                            <p className="text-xl text-gray-600 font-light mt-6 max-w-2xl mx-auto leading-relaxed">
                                {slideData.subtitle}
                            </p>
                        </div>
                    )}

                    {/* Corner accent */}
                    <div className={`absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl from-blue-600 to-blue-800 opacity-5 rounded-bl-full`} />
                </div>

                {/* Content */}
                <div className="flex-1 max-w-4xl mx-auto">
                    <div className="prose prose-lg prose-gray max-w-none">
                        <div className="text-lg text-gray-700 leading-relaxed">
                            {slideData?.content?.split('\n').map((paragraph, index) => (
                                <p key={index} className="mb-6">
                                    {paragraph}
                                </p>
                            ))}
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
            </div>
        </div>
    )
}

export default ContentSlideLayout 