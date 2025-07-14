import React from 'react'
import { z } from "zod";

const twoColumnSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Two Column Layout'),
    subtitle: z.string().min(3).max(150).optional().describe("Optional subtitle or section header"),
    leftColumn: z.object({
        title: z.string().min(2).max(50).default('Left Column'),
        content: z.string().min(10).max(1000).default('Content for the left column...').describe("Content for the left column")
    }).default({
        title: 'Left Column',
        content: 'Content for the left column...'
    }),
    rightColumn: z.object({
        title: z.string().min(2).max(50).default('Right Column'),
        content: z.string().min(10).max(1000).default('Content for the right column...').describe("Content for the right column")
    }).default({
        title: 'Right Column',
        content: 'Content for the right column...'
    }),
    backgroundImage: z.string().url().default("https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4").optional().describe("URL to background image for the slide")
})

// Standardized schema export
export const Schema = twoColumnSlideSchema

export type TwoColumnSlideData = z.infer<typeof twoColumnSlideSchema>

interface TwoColumnSlideLayoutProps {
    data?: Partial<TwoColumnSlideData>
}

const TwoColumnSlideLayout: React.FC<TwoColumnSlideLayoutProps> = ({ data }) => {
    const slideData = twoColumnSlideSchema.parse(data || {})

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

                {/* Two Columns */}
                <div className="flex-1 flex space-x-16">
                    {/* Left Column */}
                    <div className="w-1/2 flex flex-col">
                        <h2 className={`text-3xl font-semibold mb-8 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                            {slideData.leftColumn.title}
                        </h2>
                        <div className={`flex-1 text-xl leading-relaxed whitespace-pre-line ${slideData.backgroundImage ? 'text-gray-200' : 'text-gray-800'}`}>
                            {slideData.leftColumn.content}
                        </div>
                    </div>

                    {/* Divider */}
                    <div className={`w-px ${slideData.backgroundImage ? 'bg-gray-400' : 'bg-gray-300'}`} />

                    {/* Right Column */}
                    <div className="w-1/2 flex flex-col">
                        <h2 className={`text-3xl font-semibold mb-8 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                            {slideData.rightColumn.title}
                        </h2>
                        <div className={`flex-1 text-xl leading-relaxed whitespace-pre-line ${slideData.backgroundImage ? 'text-gray-200' : 'text-gray-800'}`}>
                            {slideData.rightColumn.content}
                        </div>
                    </div>
                </div>
            </div>

            {/* Decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-gradient-to-r from-blue-500 to-blue-700" />
        </div>
    )
}

export default TwoColumnSlideLayout 