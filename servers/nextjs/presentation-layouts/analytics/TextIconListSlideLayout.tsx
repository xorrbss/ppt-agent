import React from 'react'
import * as z from "zod";

export const layoutId = 'text-icon-list-slide'
export const layoutName = 'Text + Icon List Slide'
export const layoutDescription = 'A layout for displaying informational content like features, checklists, and steps with icons and descriptions.'

const listItemSchema = z.object({
    icon: z.string().min(1).max(10).meta({ description: "Icon (emoji or simple text)" }),
    heading: z.string().min(2).max(60).meta({ description: "Item heading" }),
    description: z.string().min(10).max(200).meta({ description: "Item description" }),
    status: z.enum(['default', 'completed', 'important', 'warning']).default('default').meta({ description: "Item status for styling" }),
});

const textIconListSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Features & Benefits').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(300).default('These essential features and capabilities provide comprehensive solutions to meet your business objectives and drive operational excellence.').meta({
        description: "Bottom description text",
    }),
    items: z.array(listItemSchema).min(2).max(8).default([
        {
            icon: '🚀',
            heading: 'Fast Performance',
            description: 'Optimized for speed with advanced caching and efficient algorithms to deliver exceptional user experience.',
            status: 'important'
        },
        {
            icon: '🔒',
            heading: 'Enterprise Security',
            description: 'Bank-level security with end-to-end encryption and compliance with industry standards.',
            status: 'completed'
        },
        {
            icon: '📊',
            heading: 'Advanced Analytics',
            description: 'Real-time insights and comprehensive reporting to track performance and make data-driven decisions.',
            status: 'default'
        },
        {
            icon: '🔧',
            heading: 'Easy Integration',
            description: 'Seamless integration with existing systems through RESTful APIs and pre-built connectors.',
            status: 'default'
        },
        {
            icon: '📱',
            heading: 'Mobile Responsive',
            description: 'Fully responsive design that works flawlessly across all devices and screen sizes.',
            status: 'default'
        },
        {
            icon: '⚡',
            heading: '24/7 Support',
            description: 'Round-the-clock technical support and dedicated account management for enterprise clients.',
            status: 'important'
        },
    ]).meta({
        description: "List of items with icons (2-8 items)",
    }),
    layout: z.enum(['grid', 'single-column']).default('grid').meta({
        description: "Layout style - grid for 2 columns, single-column for vertical list",
    }),
})

export const Schema = textIconListSlideSchema

export type TextIconListSlideData = z.infer<typeof textIconListSlideSchema>

interface TextIconListSlideLayoutProps {
    data?: Partial<TextIconListSlideData>
}

const TextIconListSlideLayout: React.FC<TextIconListSlideLayoutProps> = ({ data: slideData }) => {
    const items = slideData?.items || [];
    const layout = slideData?.layout || 'grid';

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'completed':
                return 'text-green-600';
            case 'important':
                return 'text-blue-600';
            case 'warning':
                return 'text-amber-600';
            default:
                return 'text-gray-900';
        }
    };

    const getBackgroundColor = (status: string) => {
        switch (status) {
            case 'completed':
                return 'bg-green-50 border-green-200';
            case 'important':
                return 'bg-blue-50 border-blue-200';
            case 'warning':
                return 'bg-amber-50 border-amber-200';
            default:
                return 'bg-white/30 border-slate-200';
        }
    };

    const getGridLayout = () => {
        if (layout === 'single-column') {
            return 'grid-cols-1';
        }
        return 'grid-cols-1 md:grid-cols-2';
    };

    const renderSVGIcon = (iconText: string) => {
        // If it's an emoji, return as is


        // For non-emoji, create a simple circle with text
        return (
            <div className="w-8 h-8 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center text-sm font-bold">
                {iconText.charAt(0).toUpperCase()}
            </div>
        );
    };

    return (
        <>
            {/* Import Google Fonts */}
            <link
                href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Nunito:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />

            <div
                className="w-full rounded-sm max-w-[1280px] shadow-md h-[720px] flex flex-col aspect-video bg-stone-100 relative z-20 mx-auto overflow-hidden"
                style={{
                    fontFamily: 'Nunito, sans-serif'
                }}
            >
                {/* Glass overlay background */}
                <div className="absolute inset-0 bg-white/20 backdrop-blur-sm rounded-sm border border-slate-200"></div>

                <div className="relative z-10 flex flex-col w-full h-full p-4 sm:p-6 lg:p-8">
                    {/* Header section */}
                    <div className="flex-shrink-0 h-12 sm:h-16 flex items-center justify-center">
                        <h1
                            className="text-3xl font-bold text-gray-900 leading-tight text-center"
                            style={{
                                fontFamily: 'Space Grotesk, sans-serif'
                            }}
                        >
                            {slideData?.title || 'Key Features & Benefits'}
                        </h1>
                    </div>

                    {/* Items Grid section */}
                    <div className="flex-1 w-full overflow-hidden">
                        <div className="h-full flex items-center justify-center">
                            <div className="w-full max-w-5xl">
                                <div className={`grid ${getGridLayout()} gap-6 content-center`}>
                                    {items.map((item, index) => (
                                        <div
                                            key={index}
                                            className={`backdrop-blur-sm rounded-xl border shadow-md p-4 sm:p-6 ${getBackgroundColor(item.status)}`}
                                        >
                                            <div className="flex items-start gap-4">
                                                {/* Icon */}
                                                <div className="flex-shrink-0 mt-1">
                                                    {renderSVGIcon(item.icon)}
                                                </div>

                                                {/* Content */}
                                                <div className="flex-1">
                                                    {/* Heading */}
                                                    <h3
                                                        className={`text-lg font-semibold leading-tight mb-2 ${getStatusColor(item.status)}`}
                                                        style={{
                                                            fontFamily: 'Space Grotesk, sans-serif'
                                                        }}
                                                    >
                                                        {item.heading}
                                                    </h3>

                                                    {/* Description */}
                                                    <p
                                                        className="text-sm text-gray-500 leading-relaxed"
                                                        style={{
                                                            fontFamily: 'Nunito, sans-serif'
                                                        }}
                                                    >
                                                        {item.description}
                                                    </p>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Bottom Description section */}
                    <div className="flex-shrink-0 h-16 sm:h-20 flex items-center justify-center">
                        <p
                            className="text-sm sm:text-base text-center text-gray-700 leading-relaxed max-w-4xl px-4"
                            style={{
                                fontFamily: 'Nunito, sans-serif'
                            }}
                        >
                            {slideData?.description || 'These essential features and capabilities provide comprehensive solutions to meet your business objectives and drive operational excellence.'}
                        </p>
                    </div>
                </div>
            </div>
        </>
    )
}

export default TextIconListSlideLayout 