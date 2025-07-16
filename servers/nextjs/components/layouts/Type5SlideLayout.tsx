import React from 'react'
import * as z from "zod";

export const layoutId = 'type5-slide'
export const layoutName = 'Type5 Slide'
export const layoutDescription = 'A two-column layout with title and description on the left, and numbered items with large numerals on the right.'

const type5SlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Points').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(500).default('Here is the main description that provides context and introduction to the numbered points on the right side.').meta({
        description: "Main description text",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(100).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(300).meta({
            description: "Item description",
        })
    })).min(2).max(3).default([
        {
            heading: 'First Key Point',
            description: 'Detailed explanation of the first important point that supports the main topic'
        },
        {
            heading: 'Second Key Point',
            description: 'Detailed explanation of the second important point with relevant information'
        },
        {
            heading: 'Third Key Point',
            description: 'Detailed explanation of the third important point that concludes the discussion'
        }
    ]).meta({
        description: "List of numbered items (2-3 items)",
    })
})

export const Schema = type5SlideSchema

export type Type5SlideData = z.infer<typeof type5SlideSchema>

interface Type5SlideLayoutProps {
    data?: Partial<Type5SlideData>
}

const Type5SlideLayout: React.FC<Type5SlideLayoutProps> = ({ data: slideData }) => {
    const items = slideData?.items || []
    const numberTranslations: string[] = ['01', '02', '03', '04', '05', '06']

    return (
        <div
            className="rounded-sm w-full max-w-[1280px] font-inter shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] flex flex-col items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"

        >
            <div className="flex flex-col lg:flex-row gap-4 sm:gap-18 md:gap-16 items-center w-full">
                {/* Left section - Title and Description */}
                <div className="lg:w-1/2 lg:space-y-8">
                    <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight">
                        {slideData?.title || 'Key Points'}
                    </h1>

                    <p className="text-base sm:text-lg lg:text-xl text-gray-700 leading-relaxed">
                        {slideData?.description || 'Here is the main description that provides context and introduction to the numbered points on the right side.'}
                    </p>
                </div>

                {/* Right section - Numbered items */}
                <div className="lg:w-1/2 relative">
                    <div className="space-y-3 lg:space-y-6">
                        {items.map((item, index) => (
                            <div
                                key={index}
                                style={{
                                    boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                                }}
                                className="rounded-lg p-3 lg:p-6 relative"
                            >
                                <div className="flex gap-6">
                                    <div className="text-[26px] lg:text-[32px] leading-[40px] px-1 font-bold mb-4 text-blue-600">
                                        {numberTranslations[index] || `0${index + 1}`}
                                    </div>
                                    <div className="space-y-1">
                                        <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-tight">
                                            {item.heading}
                                        </h3>
                                        <p className="text-sm sm:text-base lg:text-lg text-gray-700 leading-relaxed">
                                            {item.description}
                                        </p>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    )
}

export default Type5SlideLayout 