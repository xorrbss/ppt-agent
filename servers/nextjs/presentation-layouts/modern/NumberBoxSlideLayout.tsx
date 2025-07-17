import React from 'react'
import * as z from "zod";

export const layoutId = 'number-box-slide'
export const layoutName = 'Number Box Slide'
export const layoutDescription = 'A professional slide featuring numbered content boxes with titles, descriptions, and clean typography.'



const numberBoxSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Metrics').meta({
        description: "Main title of the slide",
    }),
    subtitle: z.string().min(10).max(200).optional().meta({
        description: "Optional subtitle or description",
    }),
    numberBoxes: z.array(z.object({
        number: z.string().min(1).max(20).meta({
            description: "Number or statistic to display",
        }),
        title: z.string().min(2).max(50).meta({
            description: "Title for the number",
        }),
        description: z.string().min(10).max(100).meta({
            description: "Brief description of the metric",
        })
    })).min(2).max(6).default([
        {
            number: '150+',
            title: 'Projects Completed',
            description: 'Successfully delivered across various industries'
        },
        {
            number: '98%',
            title: 'Client Satisfaction',
            description: 'Consistently exceeding expectations'
        },
        {
            number: '24/7',
            title: 'Support Available',
            description: 'Round-the-clock assistance for all clients'
        }
    ]).meta({
        description: "List of number boxes (2-6 items)",
    })
})

export const Schema = numberBoxSlideSchema

export type NumberBoxSlideData = z.infer<typeof numberBoxSlideSchema>

interface NumberBoxSlideLayoutProps {
    data?: Partial<NumberBoxSlideData>
}

const NumberBoxSlideLayout: React.FC<NumberBoxSlideLayoutProps> = ({ data: slideData }) => {
    // Parse and validate data with defaults
    // const data = numberBoxSlideSchema.parse(slideData || {})

    return (
        <div className="relative w-full aspect-[16/9] flex flex-col bg-white overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300">
            {/* Subtle background pattern */}
            <div className="absolute inset-0 opacity-[0.02] print:opacity-[0.01]">
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-gray-200 to-transparent transform -rotate-45 scale-150"></div>
            </div>

            {/* Header section */}
            <div className="text-center px-12 py-8 print:px-8 print:py-6">
                <h1 className="text-4xl font-bold text-blue-600 mb-4 leading-tight print:text-3xl">
                    {slideData?.title || 'Key Metrics'}
                </h1>
                {slideData?.subtitle && (
                    <p className="text-lg text-gray-600 max-w-2xl mx-auto leading-relaxed print:text-base">
                        {slideData.subtitle}
                    </p>
                )}
                <div className="mt-4 w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 mx-auto rounded-full"></div>
            </div>

            {/* Content section */}
            <div className="flex-1 px-12 pb-8 print:px-8 print:pb-6">
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {slideData?.numberBoxes?.map((box, index) => (
                        <div
                            key={index}
                            className="relative bg-white rounded-2xl p-6 border-blue-200 border-2 shadow-blue-200 shadow-lg print:shadow-md print:p-4"
                        >
                            {/* Number display */}
                            <div className="text-center mb-4">
                                <div className="text-4xl font-black text-blue-600 mb-2 print:text-3xl">
                                    {box.number}
                                </div>
                                <h3 className="text-xl font-semibold text-blue-600 mb-3 leading-tight print:text-lg">
                                    {box.title}
                                </h3>
                                <p className="text-gray-700 leading-relaxed text-sm print:text-xs">
                                    {box.description}
                                </p>
                            </div>

                            {/* Decorative corner accent */}
                            <div className="absolute bottom-0 right-0 w-12 h-12 bg-gradient-to-tl from-blue-600 to-blue-800 opacity-5 rounded-tl-full"></div>
                        </div>
                    ))}
                </div>
            </div>

            {/* Bottom decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800"></div>
        </div>
    )
}

export default NumberBoxSlideLayout 