import React from 'react'
import * as z from "zod";
import { IconSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'type7-slide'
export const layoutName = 'Type7 Slide'
export const layoutDescription = 'A centered title with a flexible grid of icon-based content items, adapting layout based on item count.'

const type7SlideSchema = z.object({
    title: z.string().min(3).max(50).default('Our Services').meta({
        description: "Main title of the slide",
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
    })).min(2).max(6).default([
        {
            heading: 'Professional Service',
            description: 'High-quality professional services tailored to your specific needs and requirements',
            icon: {
                icon_url_: '/static/icons/placeholder.png',
                icon_query_: 'Professional service icon'
            }
        },
        {
            heading: 'Expert Consultation',
            description: 'Expert advice and consultation from experienced professionals in the field',
            icon: {
                icon_url_: '/static/icons/placeholder.png',
                icon_query_: 'Expert consultation icon'
            }
        },
        {
            heading: 'Quality Assurance',
            description: 'Comprehensive quality assurance processes to ensure excellent results',
            icon: {
                icon_url_: '/static/icons/placeholder.png',
                icon_query_: 'Quality assurance icon'
            }
        },
        {
            heading: 'Customer Support',
            description: 'Dedicated customer support available to assist you throughout the process',
            icon: {
                icon_url_: '/static/icons/placeholder.png',
                icon_query_: 'Customer support icon'
            }
        }
    ]).meta({
        description: "List of service items (2-6 items)",
    })
})

export const Schema = type7SlideSchema

export type Type7SlideData = z.infer<typeof type7SlideSchema>

interface Type7SlideLayoutProps {
    data: Partial<Type7SlideData>
}

const Type7SlideLayout: React.FC<Type7SlideLayoutProps> = ({ data: slideData }) => {
    const { title, items } = slideData;
    const isGridLayout = items && items.length >= 4

    const getGridCols = (length: number) => {
        switch (length) {
            case 1: return 'lg:grid-cols-1';
            case 2: return 'lg:grid-cols-2';
            case 3: return 'lg:grid-cols-3';
            case 4: return 'lg:grid-cols-4';
            case 5: return 'lg:grid-cols-5';
            case 6: return 'lg:grid-cols-6';
            default: return 'lg:grid-cols-1';
        }
    }

    const renderGridContent = () => {
        return (
            <div className={`grid grid-cols-1 ${items && items.length > 4 ? 'md:grid-cols-3' : 'md:grid-cols-2'} gap-4 sm:gap-6 lg:gap-8 mt-4 lg:mt-12 w-full`}>
                {items && items.map((item, index) => (
                    <div
                        key={index}
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                        className="w-full rounded-lg p-3 lg:p-6 relative"
                    >
                        <div className="flex items-start gap-2 md:gap-4">
                            <div className="flex-shrink-0 lg:w-16">
                                <div className="w-12 h-12 lg:w-16 lg:h-16 bg-blue-600 rounded-lg flex items-center justify-center overflow-hidden">
                                    <img
                                        src={item.icon?.icon_url_ || ''}
                                        alt={item.icon?.icon_query_ || item.heading}
                                        className="w-full h-full object-cover"
                                    />
                                </div>
                            </div>
                            <div>
                                {item.heading && <h3 className="text-gray-900 text-base sm:text-lg lg:text-[24px] leading-[26px] lg:leading-[32px] font-bold mb-2">
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
            <div className={`grid grid-cols-1 sm:grid-cols-2 ${getGridCols(items?.length || 0)} w-full gap-3 lg:gap-8 mt-4 lg:mt-12`}>
                {items && items.map((item, index) => (
                    <div
                        key={index}
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                        className="w-full rounded-lg p-3 lg:p-6 relative"
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
                        <div className="lg:space-y-4 mt-2 lg:mt-4">
                            {item.heading && <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-tight text-center">
                                {item.heading}
                            </h3>}
                            {item.description && <p className="text-sm sm:text-base lg:text-lg text-gray-700 leading-relaxed text-center">
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
            className=" rounded-sm w-full max-w-[1280px] font-inter shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] flex flex-col items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"
        >
            <div className="text-center sm:pb-2 lg:pb-8 w-full">
                {title && <h1 className="text-gray-900 text-xl sm:text-2xl lg:text-[40px] leading-[36px] lg:leading-[48px] font-bold">
                    {title}
                </h1>}
            </div>

            {isGridLayout ? renderGridContent() : renderHorizontalContent()}
        </div>
    )
}

export default Type7SlideLayout 