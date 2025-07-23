import React from 'react'
import * as z from "zod";

export const layoutId = 'type2-numbered-slide'
export const layoutName = 'Type2 Numbered Slide'
export const layoutDescription = 'A content layout with title and numbered content items with large numerals and shadow boxes.'

const type2NumberedSlideSchema = z.object({
    title: z.string().min(3).max(50).default('Main Title').meta({
        description: "Main title of the slide",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(100).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(130).meta({
            description: "Item description",
        })
    })).min(2).max(3).default([
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

export const Schema = type2NumberedSlideSchema

export type Type2NumberedSlideData = z.infer<typeof type2NumberedSlideSchema>

interface Type2NumberedSlideLayoutProps {
    data: Partial<Type2NumberedSlideData>
}

const Type2NumberedSlideLayout: React.FC<Type2NumberedSlideLayoutProps> = ({ data: slideData }) => {
    const { title, items } = slideData;
    const isGridLayout = items?.length && items?.length >= 4
    const numberTranslations: string[] = ['01', '02', '03', '04', '05', '06']

    const renderGridContent = () => {
        return (
            <div className="grid grid-cols-1 bg-white lg:grid-cols-2 relative gap-4 lg:gap-8 mt-4 lg:mt-12">
                {items?.map((item, index) => (
                    <div
                        key={index}
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                        className="w-full relative shadow-lg rounded-lg p-3 lg:p-6"
                    >
                        <div className="flex gap-3">
                            <div className="text-[32px] leading-[40px] px-1 font-bold mb-4 text-blue-600">
                                {numberTranslations[index] || `0${index + 1}`}
                            </div>
                            <div className="space-y-2">
                                {item.heading && <h3 className="text-gray-900 text-base sm:text-lg lg:text-[24px] leading-[26px] lg:leading-[32px] font-bold">
                                    {item.heading}
                                </h3>}
                                {item.description && <p className="text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal">
                                    {item.description}
                                </p>}
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        )
    }

    const renderHorizontalContent = () => {
        return (
            <div className="flex flex-col lg:flex-row bg-white mt-4 lg:mt-12 w-full relative gap-4 lg:gap-8">
                {items?.map((item, index) => (
                    <div
                        key={index}
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                        className="w-full relative shadow-lg rounded-lg p-3 lg:p-6"
                    >
                        <div className="text-[32px] leading-[40px] font-semibold lg:mb-4 text-blue-600">
                            {numberTranslations[index] || `0${index + 1}`}
                        </div>
                        <div className="space-y-2 lg:space-y-4">
                            {item.heading && <h3 className=" text-gray-900 text-base sm:text-lg lg:text-[24px] leading-[26px] lg:leading-[32px] font-bold">
                                {item.heading}
                            </h3>}
                            {item.description && <p className=" text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal">
                                {item.description}
                            </p>}
                        </div>
                    </div>
                ))}
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

            {isGridLayout ? renderGridContent() : renderHorizontalContent()}
        </div>
    )
}

export default Type2NumberedSlideLayout 