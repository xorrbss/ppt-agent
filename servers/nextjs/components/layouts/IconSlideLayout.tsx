import React from 'react'
import * as z from "zod";
import { IconSchema } from './defaultSchemes';

export const layoutId = 'icon-slide'
export const layoutName = 'Icon Slide'
export const layoutDescription = 'A professional slide featuring a prominent icon with title, description, and key points.'



const iconSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Features').meta({
        description: "Main title of the slide",
    }),
    subtitle: z.string().min(10).max(200).optional().meta({
        description: "Optional subtitle or description",
    }),
    icon: IconSchema.default({
        __icon_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
        __icon_query__: 'A beautiful road in the mountains'
    }).meta({
        description: "Main slide icon",
    }),
    features: z.array(z.string().min(10).max(150)).min(2).max(6).default([
        'Advanced analytics and reporting capabilities',
        'Seamless integration with existing systems',
        'Real-time collaboration and communication',
        'Enhanced security and data protection'
    ]).meta({
        description: "List of key features (2-6 items)",
    })
})

export const Schema = iconSlideSchema

export type IconSlideData = z.infer<typeof iconSlideSchema>

interface IconSlideLayoutProps {
    data?: Partial<IconSlideData>
}

const IconSlideLayout: React.FC<IconSlideLayoutProps> = ({ data: slideData }) => {


    return (
        <div className="relative w-full aspect-[16/9] flex bg-white overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300">
            {/* Subtle background pattern for print compatibility */}
            <div className="absolute inset-0 opacity-[0.02] print:opacity-[0.01]">
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-gray-200 to-transparent transform rotate-45 scale-150"></div>
            </div>

            {/* Left side - Icon and title */}
            <div className="flex-1 flex flex-col justify-center items-center p-12 relative">
                {/* Icon container */}
                <div className="relative mb-8 p-8 rounded-3xl bg-white border-2 shadow-xl print:shadow-md">
                    <div className="absolute inset-0 bg-gradient-to-br from-blue-600 to-blue-800 opacity-5 rounded-3xl"></div>
                    <img
                        src={slideData?.icon?.__icon_url__ || ''}
                        alt={slideData?.icon?.__icon_query__ || ''}
                        className="w-24 h-24 object-contain relative z-10 print:w-20 print:h-20"
                    />
                    <div className="absolute -bottom-2 -right-2 w-6 h-6 bg-gradient-to-br from-blue-600 to-blue-800 rounded-full opacity-80"></div>
                </div>

                {/* Title */}
                <h1 className="text-4xl font-bold text-center text-blue-700 mb-4 max-w-sm leading-tight print:text-3xl">
                    {slideData?.title || 'Key Features'}
                </h1>

                {/* Subtitle */}
                {slideData?.subtitle && (
                    <p className="text-lg text-gray-600 text-center max-w-md leading-relaxed print:text-base">
                        {slideData.subtitle}
                    </p>
                )}

                {/* Decorative element */}
                <div className="mt-6 w-16 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full"></div>
            </div>

            {/* Right side - Content */}
            <div className="flex-1 flex flex-col justify-center p-12 bg-white/50 print:bg-transparent">
                {/* Features */}
                <div className="space-y-4">
                    {slideData?.features?.map((feature, index) => (
                        <div key={index} className="flex items-start space-x-4">
                            <div className="flex-shrink-0 w-8 h-8 bg-gradient-to-br from-blue-600 to-blue-800 rounded-full flex items-center justify-center mt-1 print:w-6 print:h-6">
                                <span className="text-white font-semibold text-sm print:text-xs">
                                    {index + 1}
                                </span>
                            </div>
                            <p className="text-gray-700 leading-relaxed print:text-sm">
                                {feature}
                            </p>
                        </div>
                    ))}
                </div>

                {/* Bottom accent line */}
                <div className="mt-8 w-full h-0.5 bg-gradient-to-r from-blue-600 to-blue-800 opacity-20"></div>
            </div>

            {/* Top decorative border */}
            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800"></div>
        </div>
    )
}

export default IconSlideLayout 