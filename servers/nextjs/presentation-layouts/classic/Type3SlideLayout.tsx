import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'type3-slide'
export const layoutName = 'Type3 Slide'
export const layoutDescription = 'A centered title with a grid of image cards, each containing a heading and description.'

const type3SlideSchema = z.object({
    title: z.string().min(3).max(50).default('Featured Content').meta({
        description: "Main title of the slide",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(50).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(130).meta({
            description: "Item description",
        }),
        image: ImageSchema.meta({
            description: "Item image",
        })
    })).min(2).max(3).default([
        {
            heading: 'First Feature',
            description: 'Description for the first featured item with detailed information',
            image: {
                image_url_: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                image_prompt_: 'A beautiful road in the mountains'
            }
        },
        {
            heading: 'Second Feature',
            description: 'Description for the second featured item with relevant details',
            image: {
                image_url_: 'https://cdn.pixabay.com/photo/2016/02/19/11/19/office-1209640_1280.jpg',
                image_prompt_: 'Modern office workspace'
            }
        },
        {
            heading: 'Third Feature',
            description: 'Description for the third featured item with important points',
            image: {
                image_url_: 'https://cdn.pixabay.com/photo/2017/08/10/08/47/laptop-2619235_1280.jpg',
                image_prompt_: 'Laptop with code on screen'
            }
        }
    ]).meta({
        description: "List of featured items (2-4 items)",
    })
})

export const Schema = type3SlideSchema

export type Type3SlideData = z.infer<typeof type3SlideSchema>

interface Type3SlideLayoutProps {
    data: Partial<Type3SlideData>
}

const Type3SlideLayout: React.FC<Type3SlideLayoutProps> = ({ data: slideData }) => {
    const { title, items } = slideData;

    const getGridCols = (length: number) => {
        switch (length) {
            case 1: return 'lg:grid-cols-1';
            case 2: return 'lg:grid-cols-2';
            case 3: return 'lg:grid-cols-3';
            case 4: return 'lg:grid-cols-4';
            default: return 'lg:grid-cols-1';
        }
    }

    return (
        <div
            className=" shadow-lg rounded-sm w-full max-w-[1280px] px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] font-inter flex flex-col items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"

        >
            <div className="text-center mb-4 lg:mb-16 w-full">
                {title && <h1 className="text-gray-900 text-xl sm:text-2xl lg:text-[40px] leading-[36px] lg:leading-[48px] font-bold">
                    {title}
                </h1>}
            </div>

            <div className={`grid grid-cols-1 lg:grid-cols-2 ${getGridCols(items?.length || 0)} gap-3 lg:gap-6 w-full`}>
                {items && items.map((item, index) => (
                    <div
                        key={index}
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                        className="flex flex-col w-full rounded-lg overflow-hidden relative"
                    >
                        {/* Image */}
                        <div className="max-md:h-[140px] max-lg:h-[180px] h-48 w-full">
                            <img
                                src={item.image?.image_url_ || ''}
                                alt={item.image?.image_prompt_ || item.heading}
                                className="w-full h-full object-cover"
                            />
                        </div>

                        {/* Content */}
                        <div className="space-y-2 p-3 lg:p-6">
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

export default Type3SlideLayout 