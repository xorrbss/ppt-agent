import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'comparison-slide'
export const layoutName = 'Comparison Slide'
export const layoutDescription = 'A professional slide for comparing features, options, or before/after scenarios.'

const comparisonSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Feature Comparison').meta({
        description: "Main title of the slide",
    }),
    subtitle: z.string().min(10).max(200).optional().meta({
        description: "Optional subtitle or description",
    }),
    leftColumn: z.object({
        title: z.string().min(2).max(50).meta({
            description: "Title for left column",
        }),
        icon: z.string().default('📊').meta({
            description: "Icon for left column",
        }),
        items: z.array(z.string().min(5).max(100)).min(2).max(8).meta({
            description: "List of items for left column",
        })
    }).default({
        title: 'Before',
        icon: '📊',
        items: [
            'Manual processes and workflows',
            'Limited scalability options',
            'Disconnected systems',
            'Time-consuming operations'
        ]
    }).meta({
        description: "Left comparison column",
    }),
    rightColumn: z.object({
        title: z.string().min(2).max(50).meta({
            description: "Title for right column",
        }),
        icon: z.string().default('🚀').meta({
            description: "Icon for right column",
        }),
        items: z.array(z.string().min(5).max(100)).min(2).max(8).meta({
            description: "List of items for right column",
        })
    }).default({
        title: 'After',
        icon: '🚀',
        items: [
            'Automated intelligent workflows',
            'Unlimited scaling capabilities',
            'Seamlessly integrated ecosystem',
            'Lightning-fast performance'
        ]
    }).meta({
        description: "Right comparison column",
    }),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = comparisonSlideSchema

export type ComparisonSlideData = z.infer<typeof comparisonSlideSchema>

interface ComparisonSlideLayoutProps {
    data?: Partial<ComparisonSlideData>
}

const ComparisonSlideLayout: React.FC<ComparisonSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData.backgroundImage.__image_url__})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >

            {/* Header section */}
            <div className="text-center px-12 py-6 print:px-8 print:py-4 relative z-10">
                <h1 className="text-4xl font-bold text-blue-600 mb-4 leading-tight print:text-3xl">
                    {slideData?.title || 'Feature Comparison'}
                </h1>
                {slideData?.subtitle && (
                    <p className="text-lg text-gray-600 max-w-2xl mx-auto leading-relaxed print:text-base">
                        {slideData.subtitle}
                    </p>
                )}
                <div className="mt-4 w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 mx-auto rounded-full"></div>
            </div>

            {/* Comparison section */}
            <div className="flex-1 px-12 pb-8 print:px-8 print:pb-6 relative z-10">
                <div className="flex gap-8 h-full items-stretch">
                    {/* Left Column */}
                    <div className="flex-1 bg-white/90 backdrop-blur-sm rounded-2xl p-6 shadow-xl border border-white/50 relative overflow-hidden print:shadow-md print:p-4">
                        {/* Header */}
                        <div className="text-center mb-6">
                            <div className="text-4xl mb-3 print:text-3xl">
                                {slideData?.leftColumn?.icon || '📊'}
                            </div>
                            <h3 className="text-2xl font-bold text-gray-700 mb-2 print:text-xl">
                                {slideData?.leftColumn?.title || 'Before'}
                            </h3>
                        </div>

                        {/* Items */}
                        <div className="space-y-3">
                            {slideData?.leftColumn?.items?.map((item, index) => (
                                <div key={index} className="flex items-start gap-3">
                                    <div className="w-2 h-2 bg-gray-400 rounded-full mt-2 flex-shrink-0"></div>
                                    <p className="text-gray-700 leading-relaxed text-sm print:text-xs">
                                        {item}
                                    </p>
                                </div>
                            ))}
                        </div>

                        {/* Accent */}
                        <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-gray-400 to-gray-600" />
                    </div>

                    {/* VS Divider */}
                    <div className="flex flex-col items-center justify-center">
                        <div className="w-16 h-16 bg-gradient-to-br from-blue-600 to-blue-800 rounded-full flex items-center justify-center shadow-2xl print:w-12 print:h-12">
                            <span className="text-white font-bold text-lg print:text-base">VS</span>
                        </div>
                        <div className="w-0.5 h-12 bg-gradient-to-b from-blue-600 to-blue-800 mt-4 print:h-8"></div>
                    </div>

                    {/* Right Column */}
                    <div className="flex-1 bg-gradient-to-br from-blue-50 to-white rounded-2xl p-6 shadow-xl border border-blue-200 relative overflow-hidden print:shadow-md print:p-4">
                        {/* Header */}
                        <div className="text-center mb-6">
                            <div className="text-4xl mb-3 print:text-3xl">
                                {slideData?.rightColumn?.icon || '🚀'}
                            </div>
                            <h3 className="text-2xl font-bold text-blue-600 mb-2 print:text-xl">
                                {slideData?.rightColumn?.title || 'After'}
                            </h3>
                        </div>

                        {/* Items */}
                        <div className="space-y-3">
                            {slideData?.rightColumn?.items?.map((item, index) => (
                                <div key={index} className="flex items-start gap-3">
                                    <div className="w-2 h-2 bg-blue-600 rounded-full mt-2 flex-shrink-0"></div>
                                    <p className="text-gray-700 leading-relaxed text-sm print:text-xs">
                                        {item}
                                    </p>
                                </div>
                            ))}
                        </div>

                        {/* Accent */}
                        <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800" />
                    </div>
                </div>
            </div>

            {/* Bottom decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800"></div>
        </div>
    )
}

export default ComparisonSlideLayout 