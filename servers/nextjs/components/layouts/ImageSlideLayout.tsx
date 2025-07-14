import React from 'react'
import { z } from "zod";

const imageSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Image Slide'),
    subtitle: z.string().min(3).max(150).optional().describe("Optional subtitle or caption"),
    imageUrl: z.string().url().default('https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4').describe("URL to the main image"),
    caption: z.string().min(5).max(300).optional().describe("Image caption or description"),
    layout: z.enum(['full', 'centered', 'left', 'right']).default('centered').describe("Image layout style"),
    backgroundImage: z.string().url().default("https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4").optional().describe("URL to background image for the slide")
})

// Standardized schema export
export const Schema = imageSlideSchema

export type ImageSlideData = z.infer<typeof imageSlideSchema>

interface ImageSlideLayoutProps {
    data?: Partial<ImageSlideData>
}

const ImageSlideLayout: React.FC<ImageSlideLayoutProps> = ({ data }) => {
    const slideData = imageSlideSchema.parse(data || {})

    const getImageClasses = () => {
        switch (slideData.layout) {
            case 'full':
                return 'w-full h-full object-cover'
            case 'left':
                return 'w-1/2 h-auto max-h-96 object-contain rounded-lg shadow-lg'
            case 'right':
                return 'w-1/2 h-auto max-h-96 object-contain rounded-lg shadow-lg'
            default: // centered
                return 'max-w-4xl max-h-96 w-auto h-auto object-contain rounded-lg shadow-lg'
        }
    }

    const getContainerClasses = () => {
        switch (slideData.layout) {
            case 'full':
                return 'relative w-full h-full'
            case 'left':
                return 'flex items-center space-x-16'
            case 'right':
                return 'flex items-center space-x-16 flex-row-reverse'
            default: // centered
                return 'flex flex-col items-center space-y-8'
        }
    }

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-gray-50 via-white to-gray-100 overflow-hidden"
            style={slideData.backgroundImage ? {
                backgroundImage: `url(${slideData.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >
            {slideData.backgroundImage && (
                <div className="absolute inset-0 bg-black bg-opacity-50" />
            )}

            <div className="relative z-10 flex flex-col h-full px-16 py-12">
                {/* Header - only show if not full layout */}
                {slideData.layout !== 'full' && (
                    <div className="mb-12">
                        <h1 className={`text-5xl font-bold mb-4 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                            {slideData.title}
                        </h1>
                        {slideData.subtitle && (
                            <p className={`text-2xl font-light ${slideData.backgroundImage ? 'text-gray-200' : 'text-gray-600'}`}>
                                {slideData.subtitle}
                            </p>
                        )}
                        <div className="w-24 h-1 bg-blue-600 mt-6" />
                    </div>
                )}

                {/* Content Area */}
                <div className={`flex-1 ${getContainerClasses()}`}>
                    {slideData.layout === 'full' && (
                        <div className="absolute top-12 left-16 z-10">
                            <h1 className="text-5xl font-bold mb-4 text-white drop-shadow-lg">
                                {slideData.title}
                            </h1>
                            {slideData.subtitle && (
                                <p className="text-2xl text-white drop-shadow-lg font-light">
                                    {slideData.subtitle}
                                </p>
                            )}
                        </div>
                    )}

                    <img
                        src={slideData.imageUrl}
                        alt={slideData.title}
                        className={getImageClasses()}
                    />

                    {(slideData.layout === 'left' || slideData.layout === 'right') && (
                        <div className="w-1/2 flex flex-col justify-center">
                            {slideData.caption && (
                                <p className={`text-xl leading-relaxed ${slideData.backgroundImage ? 'text-white' : 'text-gray-700'}`}>
                                    {slideData.caption}
                                </p>
                            )}
                        </div>
                    )}
                </div>

                {/* Caption for centered and full layouts */}
                {slideData.caption && (slideData.layout === 'centered' || slideData.layout === 'full') && (
                    <div className={`mt-8 ${slideData.layout === 'full' ? 'absolute bottom-12 left-16 right-16' : ''}`}>
                        <p className={`text-xl leading-relaxed text-center ${slideData.layout === 'full' ? 'text-white drop-shadow-lg' : slideData.backgroundImage ? 'text-white' : 'text-gray-700'}`}>
                            {slideData.caption}
                        </p>
                    </div>
                )}
            </div>

            {/* Decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-gradient-to-r from-blue-500 to-blue-700" />
        </div>
    )
}

export default ImageSlideLayout 