import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-templates/defaultSchemes';

export const layoutId = 'type1-slide'
export const layoutName = 'Type1 Slide'
export const layoutDescription = 'A clean two-column layout with title and description on the left and a featured image on the right.'

const type1SlideSchema = z.object({
    title: z.string().min(3).max(50).default('Hot NOT Reload Working!').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(130).default('This is a test of the hot reload system! If you can see this text, hot reload is working perfectly. Changes should appear instantly without page refresh.').meta({
        description: "Main description text",
    }),
    image: ImageSchema.default({
        __image_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
        __image_prompt__: 'A beautiful road in the mountains'
    }).meta({
        description: "Main slide image",
    })
})

export const Schema = type1SlideSchema

export type Type1SlideData = z.infer<typeof type1SlideSchema>

interface Type1SlideLayoutProps {
    data: Partial<Type1SlideData>
}

const Type1SlideLayout: React.FC<Type1SlideLayoutProps> = ({ data: slideData }) => {
    const { title, description, image } = slideData;
    return (
        <div
            className=" w-full rounded-sm max-w-[1280px] shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] max-h-[720px] flex items-center aspect-video bg-white relative z-20 mx-auto"

        >
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-8 md:gap-12 lg:gap-16 w-full">
                <div className="flex flex-col w-full items-start justify-center space-y-1 md:space-y-2 lg:space-y-6">
                    {/* Title */}
                    {title && <h1 className=" text-xl sm:text-2xl lg:text-[40px] leading-[36px] lg:leading-[48px] font-bold">
                        {title}
                    </h1>}

                    {/* Description */}
                    {description && <p className="text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal">
                        {description}
                    </p>}
                </div>

                {/* Image */}
                <div className="w-full  max-h-[600px]">
                    {image && <img
                        src={image?.__image_url__ || ''}
                        alt={image?.__image_prompt__ || title || ''}
                        className="w-full max-h-full object-cover rounded-lg shadow-md"
                    />}
                </div>
            </div>
        </div>
    )
}

export default Type1SlideLayout 