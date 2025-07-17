import React from 'react'
import * as z from "zod";
import { IconSchema } from './defaultSchemes';

export const layoutId = 'type8-slide'
export const layoutName = 'Type8 Slide'
export const layoutDescription = 'A two-column layout with title and description on the left, and icon-based items on the right.'

const type8SlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Features').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(500).default('Here is the main description that provides context and introduces the key features outlined on the right side.').meta({
        description: "Main description text",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(100).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(300).meta({
            description: "Item description",
        }),
        icon: IconSchema.default({
            __icon_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
            __icon_query__: 'Default icon'
        }).meta({
            description: "Icon for the item",
        })
    })).min(2).max(3).default([
        {
            heading: 'Advanced Features',
            description: 'Cutting-edge functionality designed to enhance productivity and user experience',
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                __icon_query__: 'Advanced features icon'
            }
        },
        {
            heading: 'Reliable Performance',
            description: 'Consistent and dependable performance across all platforms and devices',
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2016/02/19/11/19/office-1209640_1280.jpg',
                __icon_query__: 'Reliable performance icon'
            }
        },
        {
            heading: 'Secure Environment',
            description: 'Enterprise-grade security measures to protect your data and privacy',
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2017/08/10/08/47/laptop-2619235_1280.jpg',
                __icon_query__: 'Secure environment icon'
            }
        }
    ]).meta({
        description: "List of featured items (2-3 items)",
    })
})

export const Schema = type8SlideSchema

export type Type8SlideData = z.infer<typeof type8SlideSchema>

interface Type8SlideLayoutProps {
    data?: Partial<Type8SlideData>
}

const Type8SlideLayout: React.FC<Type8SlideLayoutProps> = ({ data: slideData }) => {
    const items = slideData?.items || []

    const renderItems = () => {
        if (items.length === 2) {
            // Vertical stacked layout for 2 items
            return (
                <div className="space-y-4 lg:space-y-8">
                    {items.map((item, index) => (
                        <div
                            key={index}
                            style={{
                                boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                            }}
                            className="rounded-lg p-3 lg:p-6 relative"
                        >
                            <div className="text-center mb-4">
                                <div className="w-16 h-16 lg:w-20 lg:h-20 bg-blue-600 rounded-lg flex items-center justify-center mx-auto mb-4 overflow-hidden">
                                    <img
                                        src={item.icon?.__icon_url__ || ''}
                                        alt={item.icon?.__icon_query__ || item.heading}
                                        className="w-full h-full object-cover"
                                    />
                                </div>
                            </div>
                            <div className="space-y-1 lg:space-y-3">
                                <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-tight text-center">
                                    {item.heading}
                                </h3>
                                <p className="text-sm sm:text-base lg:text-lg text-gray-700 leading-relaxed text-center">
                                    {item.description}
                                </p>
                            </div>
                        </div>
                    ))}
                </div>
            )
        } else {
            // Horizontal layout with side icons for 3+ items
            return (
                <div className="space-y-4 lg:space-y-8">
                    {items.map((item, index) => (
                        <div
                            key={index}
                            style={{
                                boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                            }}
                            className="rounded-lg p-3 lg:p-6 relative"
                        >
                            <div className="flex items-start gap-4">
                                <div className="w-[32px] md:w-[64px] h-[32px] md:h-[64px]">
                                    <div className="w-full h-full bg-blue-600 rounded-lg flex items-center justify-center overflow-hidden">
                                        <img
                                            src={item.icon?.__icon_url__ || ''}
                                            alt={item.icon?.__icon_query__ || item.heading}
                                            className="w-full h-full object-cover"
                                        />
                                    </div>
                                </div>
                                <div className="lg:space-y-3">
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
            )
        }
    }

    return (
        <div
            className=" shadow-lg w-full max-w-[1280px] rounded-sm font-inter px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] flex items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"
        >
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-8 lg:gap-16 items-center w-full">
                {/* Left section - Title and Description */}
                <div className="space-y-2 lg:space-y-6">
                    <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight">
                        {slideData?.title || 'Key Features'}
                    </h1>

                    <p className="text-base sm:text-lg lg:text-xl text-gray-700 leading-relaxed">
                        {slideData?.description || 'Here is the main description that provides context and introduces the key features outlined on the right side.'}
                    </p>
                </div>

                {/* Right section - Items */}
                <div className="relative">
                    {renderItems()}
                </div>
            </div>
        </div>
    )
}

export default Type8SlideLayout 