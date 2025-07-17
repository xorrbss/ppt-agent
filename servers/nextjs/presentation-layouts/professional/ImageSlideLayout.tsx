import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'image-slide'
export const layoutName = 'Image Slide'
export const layoutDescription = 'A professional slide featuring a prominent image with overlaid text content and call-to-action elements.'



const imageSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Our Vision').meta({
        description: "Main title of the slide",
    }),
    subtitle: z.string().min(10).max(200).optional().meta({
        description: "Optional subtitle or description",
    }),
    description: z.string().min(20).max(500).default('Transform your ideas into reality with innovative solutions that drive success and growth.').meta({
        description: "Main description text",
    }),
    image: ImageSchema.default({
        __image_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
        __image_prompt__: 'A beautiful road in the mountains'
    }).meta({
        description: "Main slide image",
    }),
})

export const Schema = imageSlideSchema

export type ImageSlideData = z.infer<typeof imageSlideSchema>

interface ImageSlideLayoutProps {
    data?: Partial<ImageSlideData>
}

const ImageSlideLayout: React.FC<ImageSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div className="relative w-full aspect-[16/9] flex bg-white overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300">
            {/* Left panel - Image */}
            <div className="flex-1 relative">
                <img
                    src={slideData?.image?.__image_url__ || ''}
                    alt={slideData?.image?.__image_prompt__ || ''}
                    className="w-full h-full object-cover"
                />
                {/* Overlay gradient */}
                <div className="absolute inset-0 bg-gradient-to-r from-black/70 via-black/50 to-transparent"></div>

                {/* Content overlay */}
                <div className="absolute inset-0 flex flex-col justify-center p-12 text-white print:p-8">
                    {/* Title */}
                    <h1 className="text-5xl font-bold mb-6 leading-tight print:text-4xl">
                        {slideData?.title || 'Our Vision'}
                    </h1>

                    {/* Subtitle */}
                    {slideData?.subtitle && (
                        <p className="text-xl text-gray-200 mb-6 max-w-xl leading-relaxed print:text-lg">
                            {slideData.subtitle}
                        </p>
                    )}

                    {/* Description */}
                    <p className="text-lg text-gray-100 mb-8 max-w-2xl leading-relaxed print:text-base">
                        {slideData?.description || 'Transform your ideas into reality with innovative solutions that drive success and growth.'}
                    </p>



                    {/* Decorative line */}
                    <div className="mt-8 w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full"></div>
                </div>
            </div>

            {/* Right panel for additional context */}
            <div className="w-80 bg-white/95 backdrop-blur-sm flex flex-col justify-center p-8 print:w-64 print:bg-white print:p-6">
                {/* Feature card */}
                <div className="space-y-6">
                    <div className="flex items-center space-x-4 p-4 rounded-xl bg-white shadow-md print:shadow-sm">
                        <div className="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center flex-shrink-0 print:w-10 print:h-10">
                            <span className="text-blue-600 font-bold text-lg print:text-base">✓</span>
                        </div>
                        <div>
                            <h3 className="font-semibold text-gray-900 text-sm print:text-xs">Professional Quality</h3>
                            <p className="text-gray-600 text-xs print:text-xs">Excellence in every detail</p>
                        </div>
                    </div>
                </div>

                {/* Bottom accent line */}
                <div className="mt-8 w-full h-2 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full opacity-20"></div>
            </div>

            {/* Top decorative border */}
            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800"></div>
        </div>
    )
}

export default ImageSlideLayout 