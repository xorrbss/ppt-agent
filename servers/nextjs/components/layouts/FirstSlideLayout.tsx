


import React from 'react'
import { z } from "zod";

const firstSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Welcome to Your Presentation'),
    subtitle: z.string().min(3).max(150).optional().describe("Optional subtitle or tagline"),
    author: z.string().min(2).max(50).default('Your Name').describe("Author or presenter name"),
    organization: z.string().min(2).max(100).optional().describe("Organization or company name"),
    date: z.string().default(new Date().toLocaleDateString()).describe("Presentation date"),
    logoUrl: z.string().url().optional().describe("URL to company/organization logo"),
    backgroundImage: z.string().url().default("https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4").optional().describe("URL to background image for the slide")
})

// Standardized schema export
export const Schema = firstSlideSchema

export type FirstSlideData = z.infer<typeof firstSlideSchema>

interface FirstSlideLayoutProps {
    data?: Partial<FirstSlideData>
}

const FirstSlideLayout: React.FC<FirstSlideLayoutProps> = ({ data }) => {
    const slideData = firstSlideSchema.parse(data || {})

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col justify-center items-center bg-gradient-to-br from-blue-50 via-white to-blue-100 overflow-hidden"
            style={slideData.backgroundImage ? {
                backgroundImage: `url(${slideData.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >
            {slideData.backgroundImage && (
                <div className="absolute inset-0 bg-black bg-opacity-50" />
            )}

            <div className="relative z-10 text-center max-w-5xl mx-auto px-16 py-12">
                {/* Logo */}
                {slideData.logoUrl && (
                    <div className="mb-8">
                        <img
                            src={slideData.logoUrl}
                            alt="Logo"
                            className="h-20 w-auto mx-auto"
                        />
                    </div>
                )}

                {/* Main Title */}
                <h1 className={`text-6xl font-bold mb-8 leading-tight ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                    {slideData.title}
                </h1>

                {/* Subtitle */}
                {slideData.subtitle && (
                    <p className={`text-3xl mb-16 font-light ${slideData.backgroundImage ? 'text-gray-200' : 'text-gray-600'}`}>
                        {slideData.subtitle}
                    </p>
                )}

                {/* Author and Organization */}
                <div className={`text-2xl mb-6 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                    <div className="font-semibold">{slideData.author}</div>
                    {slideData.organization && (
                        <div className={`mt-2 text-xl ${slideData.backgroundImage ? 'text-gray-300' : 'text-gray-600'}`}>
                            {slideData.organization}
                        </div>
                    )}
                </div>

                {/* Date */}
                <div className={`text-lg ${slideData.backgroundImage ? 'text-blue-300' : 'text-blue-600'}`}>
                    {slideData.date}
                </div>
            </div>

            {/* Decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-gradient-to-r from-blue-500 to-blue-700" />
        </div>
    )
}

export default FirstSlideLayout

