import React from 'react'
import * as z from "zod";

export const layoutId = 'type6-slide'
export const layoutName = 'Type6 Slide'
export const layoutDescription = 'A centered title with a flexible grid of icon-based content items, adapting layout based on item count.'

const type6SlideSchema = z.object({
    title: z.string().min(3).max(100).default('Our Services').meta({
        description: "Main title of the slide",
    }),
    items: z.array(z.object({
        heading: z.string().min(2).max(100).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(300).meta({
            description: "Item description",
        }),
        icon: z.string().default('⭐').meta({
            description: "Icon emoji for the item",
        })
    })).min(2).max(6).default([
        {
            heading: 'Professional Service',
            description: 'High-quality professional services tailored to your specific needs and requirements',
            icon: '🎯'
        },
        {
            heading: 'Expert Consultation',
            description: 'Expert advice and consultation from experienced professionals in the field',
            icon: '💡'
        },
        {
            heading: 'Quality Assurance',
            description: 'Comprehensive quality assurance processes to ensure excellent results',
            icon: '✅'
        },
        {
            heading: 'Customer Support',
            description: 'Dedicated customer support available to assist you throughout the process',
            icon: '🤝'
        }
    ]).meta({
        description: "List of service items (2-6 items)",
    })
})

export const Schema = type6SlideSchema

export type Type6SlideData = z.infer<typeof type6SlideSchema>

interface Type6SlideLayoutProps {
    data?: Partial<Type6SlideData>
}

const Type6SlideLayout: React.FC<Type6SlideLayoutProps> = ({ data: slideData }) => {
    const items = slideData?.items || []
    const isGridLayout = items.length >= 4

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
            <div className={`grid grid-cols-1 ${items.length > 4 ? 'md:grid-cols-3' : 'md:grid-cols-2'} gap-4 sm:gap-6 lg:gap-8 mt-4 lg:mt-12 w-full`}>
                {items.map((item, index) => (
                    <div
                        key={index}
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                        className="w-full rounded-lg p-3 lg:p-6 relative"
                    >
                        <div className="flex items-start gap-2 mg:gap-4">
                            <div className="flex-shrink-0 lg:w-16">
                                <div className="w-12 h-12 lg:w-16 lg:h-16 bg-blue-600 rounded-lg flex items-center justify-center text-white text-xl lg:text-2xl">
                                    {item.icon}
                                </div>
                            </div>
                            <div>
                                <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-tight mb-2">
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

    const renderHorizontalContent = () => {
        return (
            <div className={`grid grid-cols-1 sm:grid-cols-2 ${getGridCols(items.length)} w-full gap-3 lg:gap-8 mt-4 lg:mt-12`}>
                {items.map((item, index) => (
                    <div
                        key={index}
                        style={{
                            boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                        }}
                        className="w-full rounded-lg p-3 lg:p-6 relative"
                    >
                        <div className="text-center mb-4">
                            <div className="w-16 h-16 lg:w-20 lg:h-20 bg-blue-600 rounded-lg flex items-center justify-center text-white text-2xl lg:text-3xl mx-auto mb-4">
                                {item.icon}
                            </div>
                        </div>
                        <div className="lg:space-y-4 mt-2 lg:mt-4">
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
    }

    return (
        <div
            className="slide-container rounded-sm w-full max-w-[1280px] font-inter shadow-lg px-3 sm:px-12 lg:px-20 py-[10px] sm:py-[40px] lg:py-[86px] flex flex-col items-center justify-center max-h-[720px] aspect-video bg-white relative z-20 mx-auto"

        >
            <div className="text-center sm:pb-2 lg:pb-8 w-full">
                <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight">
                    {slideData?.title || 'Our Services'}
                </h1>
            </div>

            {isGridLayout ? renderGridContent() : renderHorizontalContent()}
        </div>
    )
}

export default Type6SlideLayout 