import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'intro-slide'
export const layoutName = 'Intro Slide'
export const layoutDescription = 'A 2-1 split layout featuring title, description, and presenter info on the left (2/3), and full-height image on the right (1/3).'

const introSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Analytics Dashboard').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(500).default('Welcome to our comprehensive analytics overview. This dashboard provides key insights and metrics to help you make data-driven decisions for your business growth.').meta({
        description: "Main description text",
    }),
    presenterName: z.string().min(2).max(50).default('Suraj Jha').meta({
        description: "Name of the presenter",
    }),
    presentationDate: z.string().min(2).max(50).default('December 2024').meta({
        description: "Date of the presentation",
    }),
    image: ImageSchema.default({
        __image_url__: 'https://cdn.pixabay.com/photo/2016/11/27/21/42/stock-1863880_1280.jpg',
        __image_prompt__: 'Analytics dashboard with charts and graphs'
    }).meta({
        description: "Main slide image",
    })
})

export const Schema = introSlideSchema

export type IntroSlideData = z.infer<typeof introSlideSchema>

interface IntroSlideLayoutProps {
    data?: Partial<IntroSlideData>
}

const IntroSlideLayout: React.FC<IntroSlideLayoutProps> = ({ data: slideData }) => {
    // Generate initials from presenter name
    const getInitials = (name: string) => {
        return name.split(' ').map(word => word.charAt(0).toUpperCase()).join('');
    };

    const presenterInitials = getInitials(slideData?.presenterName || 'Suraj Jha');

    return (
        <>
            {/* Import Google Fonts */}
            <link 
                href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Nunito:wght@400;500;600;700&display=swap" 
                rel="stylesheet"
            />
            
            <div
                className="w-full rounded-sm max-w-[1280px] shadow-md max-h-[720px] flex items-center aspect-video bg-stone-100 relative z-20 mx-auto"
                style={{
                    fontFamily: 'Nunito, sans-serif'
                }}
            >
                {/* Glass overlay background */}
                <div className="absolute inset-0 bg-white/20 backdrop-blur-sm rounded-sm border border-slate-200"></div>
                
                <div className="relative z-10 flex w-full h-full">
                    {/* Left section - 2/3 width */}
                    <div className="w-2/3 flex flex-col justify-center space-y-1 md:space-y-2 lg:space-y-6 pl-8 sm:pl-16 lg:pl-24 py-[10px] sm:py-[40px] lg:py-[86px] pr-6 lg:pr-12">
                        {/* Title */}
                        <h1 
                            className="text-3xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight"
                            style={{
                                fontFamily: 'Space Grotesk, sans-serif'
                            }}
                        >
                            {slideData?.title || 'Analytics Dashboard'}
                        </h1>

                        {/* Description */}
                        <p 
                            className="text-base sm:text-lg lg:text-xl text-gray-700 leading-relaxed"
                            style={{
                                fontFamily: 'Nunito, sans-serif'
                            }}
                        >
                            {slideData?.description || 'Welcome to our comprehensive analytics overview. This dashboard provides key insights and metrics to help you make data-driven decisions for your business growth.'}
                        </p>

                        {/* Presenter Box */}
                        <div className="bg-white/30 backdrop-blur-sm rounded-lg p-4 lg:p-6 border border-slate-200 shadow-md">
                            <div className="flex items-center gap-4">
                                {/* Custom Initials Icon */}
                                <div className="w-10 h-10 lg:w-12 lg:h-12 bg-blue-500 rounded-full flex items-center justify-center">
                                    <span 
                                        className="text-white font-bold text-sm lg:text-base"
                                        style={{
                                            fontFamily: 'Space Grotesk, sans-serif'
                                        }}
                                    >
                                        {presenterInitials}
                                    </span>
                                </div>
                                
                                {/* Presenter Info */}
                                <div className="flex flex-col">
                                    <span 
                                        className="text-xl lg:text-2xl font-bold text-gray-900"
                                        style={{
                                            fontFamily: 'Space Grotesk, sans-serif'
                                        }}
                                    >
                                        {slideData?.presenterName || 'Suraj Jha'}
                                    </span>
                                    <span 
                                        className="text-sm lg:text-base text-gray-600 font-medium"
                                        style={{
                                            fontFamily: 'Nunito, sans-serif'
                                        }}
                                    >
                                        {slideData?.presentationDate || 'December 2024'}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Right section - 1/3 width */}
                    <div className="w-1/3 h-full">
                        <img
                            src={slideData?.image?.__image_url__ || ''}
                            alt={slideData?.image?.__image_prompt__ || slideData?.title || ''}
                            className="w-full h-full object-cover"
                        />
                    </div>
                </div>
            </div>
        </>
    )
}

export default IntroSlideLayout 