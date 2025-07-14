import React from 'react'
import { z } from "zod";

const contentSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Content Title'),
    content: z.string().min(10).max(2000).default('Your main content goes here...').describe("Main content text for the slide"),
    subtitle: z.string().min(3).max(150).optional().default('Subtitle of the slide').describe("Optional subtitle or section header"),
    backgroundImage: z.string().url().default("https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4").optional().describe("URL to background image for the slide")
})

// Standardized schema export
export const Schema = contentSlideSchema

export type ContentSlideData = z.infer<typeof contentSlideSchema>

interface ContentSlideLayoutProps {
    data?: Partial<ContentSlideData>
}

const ContentSlideLayout: React.FC<ContentSlideLayoutProps> = ({ data }) => {
    const slideData = contentSlideSchema.parse(data || {})
    console.log(slideData)

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-gray-50 via-white to-gray-100 overflow-hidden"
            style={slideData.backgroundImage ? {
                backgroundImage: `url(${slideData.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >
            {slideData.backgroundImage && (
                <div className="absolute inset-0 bg-black bg-opacity-50" />
            )}

            <div className="relative z-10 flex flex-col h-full px-16 py-12">
                {/* Header */}
                <div className="mb-12">
                    <h1 className={`text-5xl font-bold mb-4 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                        {slideData.title}
                    </h1>
                    {slideData.subtitle && (
                        <p className={`text-2xl font-light ${slideData.backgroundImage ? 'text-gray-200' : 'text-gray-600'}`}>
                            {slideData.subtitle}
                        </p>
                    )}
                    <div className="w-24 h-1 bg-blue-600 mt-6" />
                </div>

                {/* Content Area */}
                <div className="flex-1 flex items-center">
                    <div className="w-full max-w-5xl">
                        <div className={`text-xl leading-relaxed whitespace-pre-line ${slideData.backgroundImage ? 'text-white' : 'text-gray-800'}`}>
                            {slideData.content}
                        </div>
                    </div>
                </div>
            </div>

            {/* Decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-gradient-to-r from-blue-500 to-blue-700" />
        </div>
    )
}

export default ContentSlideLayout 