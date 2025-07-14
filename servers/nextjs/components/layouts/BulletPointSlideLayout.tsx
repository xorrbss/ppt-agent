import React from 'react'
import { z } from "zod";

export const Schema = z.object({
    title: z.string().min(3).max(100).default('Key Points'),
    subtitle: z.string().min(3).max(150).optional().describe("Optional subtitle or section header"),
    bulletPoints: z.array(z.string().min(5).max(200)).min(1).max(8).default([
        'First important point',
        'Second key insight',
        'Third crucial element'
    ]).describe("List of bullet points (1-8 items)"),
    backgroundImage: z.string().url().optional().default("https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4").describe("URL to background image for the slide")
})



export type BulletPointSlideData = z.infer<typeof Schema>

interface BulletPointSlideLayoutProps {
    data?: Partial<BulletPointSlideData>
}

const BulletPointSlideLayout: React.FC<BulletPointSlideLayoutProps> = ({ data }) => {
    const slideData = Schema.parse(data || {})

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

                {/* Bullet Points */}
                <div className="flex-1 flex items-center">
                    <div className="w-full max-w-4xl">
                        <ul className="space-y-8">
                            {slideData.bulletPoints.map((point, index) => (
                                <li key={index} className="flex items-start group">
                                    <div className="flex-shrink-0 w-4 h-4 bg-blue-600 rounded-full mt-4 mr-8 group-hover:bg-blue-700 transition-colors" />
                                    <span className={`text-2xl leading-relaxed ${slideData.backgroundImage ? 'text-white' : 'text-gray-800'}`}>
                                        {point}
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </div>
                </div>
            </div>

            {/* Decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-gradient-to-r from-blue-500 to-blue-700" />
        </div>
    )
}

export default BulletPointSlideLayout 