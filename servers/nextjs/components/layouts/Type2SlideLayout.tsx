import React from 'react'
import * as z from "zod";

export const layoutId = 'type2-slide'
export const layoutName = 'Type2 Slide'
export const layoutDescription = 'A flexible content layout with title and multiple content items in default presentation style.'

const type2SlideSchema = z.object({
    title: z.string().min(3).max(100).default('Main Title').meta({
        description: "Main title of the slide",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(100).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(300).meta({
            description: "Item description",
        })
    })).min(2).max(4).default([
        {
            heading: 'First Point',
            description: 'Description for the first key point that explains important details'
        },
        {
            heading: 'Second Point',
            description: 'Description for the second key point with relevant information'
        },
        {
            heading: 'Third Point',
            description: 'Description for the third key point highlighting crucial aspects'
        }
    ]).meta({
        description: "List of content items (2-4 items)",
    })
})

export const Schema = type2SlideSchema

export type Type2SlideData = z.infer<typeof type2SlideSchema>

interface Type2SlideLayoutProps {
    data?: Partial<Type2SlideData>
}

const Type2SlideLayout: React.FC<Type2SlideLayoutProps> = ({ data: slideData }) => {
    const items = slideData?.items || []
    const isGridLayout = items.length >= 4

    const renderGridContent = () => {
        return (
            <div className="grid grid-cols-1 lg:grid-cols-2 relative gap-6 md:gap-12 mt-4 lg:mt-12">
                {items.map((item, index) => (
                    <div key={index} className="w-full relative p-3 lg:p-6 rounded-md"
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                    >
                        <div className="space-y-2">
                            <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-tight">
                                {item.heading}
                            </h3>
                            <p className="text-sm sm:text-base lg:text-lg text-gray-700 leading-relaxed">
                                {item.description}
                            </p>
                        </div>
                    </div>
                ))}
            </div>
        )
    }

    const renderHorizontalContent = () => {
        return (
            <div className="flex flex-col lg:flex-row mt-4 lg:mt-12 w-full relative gap-12">
                {items.map((item, index) => (
                    <div key={index} className="w-full relative p-3 lg:p-6 rounded-md"
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                    >
                        <div className="space-y-2 lg:space-y-4">
                            <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-tight">
                                {item.heading}
                            </h3>
                            <p className="text-sm sm:text-base lg:text-lg text-gray-700 leading-relaxed">
                                {item.description}
                            </p>
                        </div>
                    </div>
                ))}
            </div>
        )
    }

    return (
        <div
            className="slide-container rounded-sm max-w-[1280px] w-full shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] flex flex-col items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"

        >
            <div className="text-center lg:pb-8 w-full">
                <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight">
                    {slideData?.title || 'Main Title'}
                </h1>
            </div>

            {isGridLayout ? renderGridContent() : renderHorizontalContent()}
        </div>
    )
}

export default Type2SlideLayout 