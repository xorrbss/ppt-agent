import React from 'react'
import * as z from "zod";
import { ImageSchema } from './defaultSchemes';

export const layoutId = 'type1-slide'
export const layoutName = 'Type1 Slide'
export const layoutDescription = 'A clean two-column layout with title and description on the left and a featured image on the right.'

const type1SlideSchema = z.object({
    title: z.string().min(3).max(100).default('Sample Title').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(500).default('Your description content goes here. This layout provides a clean and professional way to present content with supporting imagery.').meta({
        description: "Main description text",
    }),
    image: ImageSchema.default({
        url: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
        prompt: 'A beautiful road in the mountains'
    }).meta({
        description: "Main slide image",
    })
})

export const Schema = type1SlideSchema

export type Type1SlideData = z.infer<typeof type1SlideSchema>

interface Type1SlideLayoutProps {
    data?: Partial<Type1SlideData>
}

const Type1SlideLayout: React.FC<Type1SlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="slide-container w-full rounded-sm max-w-[1280px] shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] max-h-[720px] flex items-center aspect-video bg-white relative z-20 mx-auto"

        >
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-8 md:gap-12 lg:gap-16 w-full">
                <div className="flex flex-col w-full items-start justify-center space-y-1 md:space-y-2 lg:space-y-6">
                    {/* Title */}
                    <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight">
                        {slideData?.title || 'Sample Title'}
                    </h1>

                    {/* Description */}
                    <p className="text-base sm:text-lg lg:text-xl text-gray-700 leading-relaxed">
                        {slideData?.description || 'Your description content goes here. This layout provides a clean and professional way to present content with supporting imagery.'}
                    </p>
                </div>

                {/* Image */}
                <div className="w-full h-full min-h-[200px] lg:min-h-[300px]">
                    <img
                        src={slideData?.image?.url || ''}
                        alt={slideData?.image?.prompt || slideData?.title || ''}
                        className="w-full h-full object-cover rounded-lg shadow-md"
                    />
                </div>
            </div>
        </div>
    )
}

export default Type1SlideLayout 