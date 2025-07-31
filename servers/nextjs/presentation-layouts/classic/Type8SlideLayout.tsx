import React from 'react'
import * as z from "zod";
import { IconSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'type8-slide'
export const layoutName = 'Type8 Slide'
export const layoutDescription = 'A two-column layout with title and description on the left, and icon-based items on the right.'

const type8SlideSchema = z.object({
    title: z.string().min(3).max(50).default('Key Features').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(130).default('Here is the main description that provides context and introduces the key features outlined on the right side.').meta({
        description: "Main description text",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(50).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(130).meta({
            description: "Item description",
        }),
        icon: IconSchema.default({
            icon_url_: '/static/icons/placeholder.png',
            icon_query_: 'Default icon'
        }).meta({
            description: "Icon for the item",
        })
    })).min(2).max(3).default([
        {
            heading: 'Advanced Features',
            description: 'Cutting-edge functionality designed to enhance productivity and user experience',
            icon: {
                icon_url_: '/static/icons/placeholder.png',
                icon_query_: 'Advanced features icon'
            }
        },
        {
            heading: 'Reliable Performance',
            description: 'Consistent and dependable performance across all platforms and devices',
            icon: {
                icon_url_: '/static/icons/placeholder.png',
                icon_query_: 'Reliable performance icon'
            }
        },
        {
            heading: 'Secure Environment',
            description: 'Enterprise-grade security measures to protect your data and privacy',
            icon: {
                icon_url_: '/static/icons/placeholder.png',
                icon_query_: 'Secure environment icon'
            }
        }
    ]).meta({
        description: "List of featured items (2-3 items)",
    })
})

export const Schema = type8SlideSchema

export type Type8SlideData = z.infer<typeof type8SlideSchema>

interface Type8SlideLayoutProps {
    data: Partial<Type8SlideData>
}

const Type8SlideLayout: React.FC<Type8SlideLayoutProps> = ({ data: slideData }) => {
    const { title, description, items } = slideData;

    const renderItems = () => {
        if (items && items.length === 2) {
            // Vertical stacked layout for 2 items
            return (
                <div className="space-y-4 lg:space-y-8">
                    {items && items.map((item, index) => (
                        <div
                            key={index}
                            style={{
                                boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                            }}
                            className="rounded-lg p-3 lg:p-6 relative"
                        >
                            <div className="text-center mb-4">
                                <div className="w-16 h-16 lg:w-20 lg:h-20 bg-blue-600 rounded-lg flex items-center justify-center mx-auto mb-4 overflow-hidden">
                                    {item.icon?.icon_url_ && <img
                                        src={item.icon?.icon_url_ || ''}
                                        alt={item.icon?.icon_query_ || item.heading}
                                        className="w-full h-full object-cover"
                                    />}
                                </div>
                            </div>
                            <div className="space-y-1 lg:space-y-3">
                                {item.heading && <h3 className="text-gray-900 text-base sm:text-lg lg:text-[24px] leading-[26px] lg:leading-[32px] font-bold text-center">
                                    {item.heading}
                                </h3>}
                                {item.description && <p className="text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal text-center">
                                    {item.description}
                                </p>}
                            </div>
                        </div>
                    ))}
                </div>
            )
        } else {
            // Horizontal layout with side icons for 3+ items
            return (
                <div className="space-y-4 lg:space-y-8">
                    {items && items.map((item, index) => (
                        <div
                            key={index}
                            style={{
                                boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                            }}
                            className="rounded-lg p-3 lg:p-6 relative"
                        >
                            <div className="flex items-start gap-4">
                                <div className="w-[64px] h-[64px]">
                                    <div className="w-full h-full bg-blue-600 rounded-lg flex items-center justify-center overflow-hidden">
                                        {item.icon?.icon_url_ && <img
                                            src={item.icon?.icon_url_ || ''}
                                            alt={item.icon?.icon_query_ || item.heading}
                                            className="w-full h-full object-cover"
                                        />}
                                    </div>
                                </div>
                                <div className="w-[calc(100%-70px)] lg:space-y-3">
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
    }

    return (
        <div
            className=" shadow-lg w-full max-w-[1280px] rounded-sm font-inter px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] flex items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"
        >
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-8 lg:gap-16 items-center w-full">
                {/* Left section - Title and Description */}
                <div className="space-y-2 lg:space-y-6">
                    {title && <h1 className="text-gray-900 text-xl sm:text-2xl lg:text-[40px] leading-[36px] lg:leading-[48px] font-bold">
                        {title}
                    </h1>}

                    {description && <p className="text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal">
                        {description}
                    </p>}
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