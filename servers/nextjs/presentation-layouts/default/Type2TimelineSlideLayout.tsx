import React from 'react'
import * as z from "zod";

export const layoutId = 'type2-timeline-slide'
export const layoutName = 'Type2 Timeline Slide'
export const layoutDescription = 'A timeline layout with title and content items arranged horizontally with numbered circles and connecting line.'

const type2TimelineSlideSchema = z.object({
    title: z.string().min(3).max(50).default('Main Title').meta({
        description: "Main title of the slide",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(50).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(130).meta({
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

export const Schema = type2TimelineSlideSchema

export type Type2TimelineSlideData = z.infer<typeof type2TimelineSlideSchema>

interface Type2TimelineSlideLayoutProps {
    data: Partial<Type2TimelineSlideData>
}

const Type2TimelineSlideLayout: React.FC<Type2TimelineSlideLayoutProps> = ({ data: slideData }) => {
    const { title, items } = slideData;

    const renderTimelineContent = () => {
        return (
            <div className="w-full flex flex-col relative mt-4 lg:mt-16">
                {/* Timeline Header with Numbers and Line */}
                <div className="relative flex justify-between w-[85%] mx-auto items-center mb-8 px-8">
                    {/* Horizontal Line */}
                    <div className="absolute z-10 top-1/2 w-[87%] left-1/2 -translate-x-1/2 h-[2px] bg-blue-600" />

                    {/* Timeline Numbers */}
                    {items && items.map((_, index) => (
                        <div
                            key={`timeline-${index}`}
                            className="relative z-10 w-12 h-12 rounded-full bg-blue-600 px-1 text-white flex items-center justify-center font-bold text-lg"
                        >
                            <span> `0${index + 1}`</span>
                        </div>
                    ))}
                </div>

                {/* Timeline Content */}
                <div className="flex justify-between gap-8">
                    {items && items.map((item, index) => (
                        <div key={index} className="flex-1 text-center relative">
                            <div className="space-y-4">
                                {item.heading && <h3 className="text-gray-900 text-base sm:text-lg lg:text-[24px] leading-[26px] lg:leading-[32px] font-bold">
                                    {item.heading}
                                </h3>}
                                {item.description && <p className="text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal">
                                    {item.description}
                                </p>}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        )
    }

    return (
        <div
            className=" rounded-sm max-w-[1280px] w-full shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] flex flex-col items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"
        >
            <div className="text-center lg:pb-8 w-full">
                {title && <h1 className="text-gray-900 text-xl sm:text-2xl lg:text-[40px] leading-[36px] lg:leading-[48px] font-bold">
                    {title}
                </h1>}
            </div>

            {renderTimelineContent()}
        </div>
    )
}

export default Type2TimelineSlideLayout 