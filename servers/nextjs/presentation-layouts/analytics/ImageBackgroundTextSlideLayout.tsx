import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'image-background-text-slide'
export const layoutName = 'Image Background + Text Slide'
export const layoutDescription = 'A layout for quotes, key messages, and mood slides with full-slide background image and overlaid text.'

const imageBackgroundTextSlideSchema = z.object({
    title: z.string().min(3).max(200).default('Success is not final, failure is not fatal: it is the courage to continue that counts.').meta({
        description: "Main quote or message text",
    }),
    subtitle: z.string().optional().default('Winston Churchill').meta({
        description: "Optional subtitle, author, or attribution",
    }),
    backgroundImage: ImageSchema.default({
        __image_url__: 'https://cdn.pixabay.com/photo/2016/12/02/02/10/idea-1876659_1280.jpg',
        __image_prompt__: 'Inspirational workspace with lightbulb and motivation'
    }).meta({
        description: "Background image for the slide",
    }),
    overlayOpacity: z.number().min(0.3).max(0.8).default(0.5).meta({
        description: "Dark overlay opacity (0.3-0.8)",
    }),
    textAlignment: z.enum(['left', 'center', 'right']).default('center').meta({
        description: "Text alignment",
    }),
    textSize: z.enum(['large', 'extra-large', 'huge']).default('large').meta({
        description: "Text size variant",
    }),
    textColor: z.enum(['white', 'light-gray', 'yellow', 'blue']).default('white').meta({
        description: "Text color theme",
    }),
})

export const Schema = imageBackgroundTextSlideSchema

export type ImageBackgroundTextSlideData = z.infer<typeof imageBackgroundTextSlideSchema>

interface ImageBackgroundTextSlideLayoutProps {
    data?: Partial<ImageBackgroundTextSlideData>
}

const ImageBackgroundTextSlideLayout: React.FC<ImageBackgroundTextSlideLayoutProps> = ({ data: slideData }) => {
    const getTextAlignment = () => {
        switch (slideData?.textAlignment) {
            case 'left':
                return 'text-left items-start justify-start pl-8 sm:pl-16 lg:pl-24';
            case 'right':
                return 'text-right items-end justify-end pr-8 sm:pr-16 lg:pr-24';
            default:
                return 'text-center items-center justify-center';
        }
    };

    const getTextSize = () => {
        switch (slideData?.textSize) {
            case 'extra-large':
                return 'text-3xl sm:text-4xl lg:text-5xl xl:text-6xl';
            case 'huge':
                return 'text-4xl sm:text-5xl lg:text-6xl xl:text-7xl';
            default:
                return 'text-2xl sm:text-3xl lg:text-4xl xl:text-5xl';
        }
    };

    const getTextColor = () => {
        switch (slideData?.textColor) {
            case 'light-gray':
                return 'text-gray-100';
            case 'yellow':
                return 'text-yellow-300';
            case 'blue':
                return 'text-blue-300';
            default:
                return 'text-white';
        }
    };

    const getOverlayOpacity = () => {
        const opacity = slideData?.overlayOpacity || 0.5;
        return `rgba(0, 0, 0, ${opacity})`;
    };

    return (
        <>
            {/* Import Google Fonts */}
            <link 
                href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Nunito:wght@400;500;600;700&display=swap" 
                rel="stylesheet"
            />
            
            <div
                className="w-full rounded-sm max-w-[1280px] shadow-md h-[720px] flex flex-col aspect-video relative z-20 mx-auto overflow-hidden"
                style={{
                    fontFamily: 'Nunito, sans-serif'
                }}
            >
                {/* Background Image */}
                <div className="absolute inset-0">
                    <img
                        src={slideData?.backgroundImage?.__image_url__ || ''}
                        alt={slideData?.backgroundImage?.__image_prompt__ || 'Background image'}
                        className="w-full h-full object-cover"
                    />
                </div>

                {/* Dark Overlay */}
                <div 
                    className="absolute inset-0 z-10"
                    style={{
                        backgroundColor: getOverlayOpacity()
                    }}
                />
                
                {/* Text Overlay */}
                <div className={`absolute inset-0 z-20 flex flex-col ${getTextAlignment()} p-8 sm:p-12 lg:p-16`}>
                    <div className="max-w-4xl">
                        {/* Main Title/Quote */}
                        <h1 
                            className={`${getTextSize()} ${getTextColor()} font-bold leading-tight mb-4 sm:mb-6 lg:mb-8`}
                            style={{
                                fontFamily: 'Space Grotesk, sans-serif',
                                textShadow: '2px 2px 4px rgba(0,0,0,0.3)'
                            }}
                        >
                            {slideData?.title || 'Success is not final, failure is not fatal: it is the courage to continue that counts.'}
                        </h1>

                        {/* Subtitle/Attribution */}
                        {slideData?.subtitle && (
                            <p 
                                className={`text-lg sm:text-xl lg:text-2xl ${getTextColor()} font-medium opacity-90`}
                                style={{
                                    fontFamily: 'Nunito, sans-serif',
                                    textShadow: '1px 1px 2px rgba(0,0,0,0.3)'
                                }}
                            >
                                — {slideData.subtitle}
                            </p>
                        )}
                    </div>
                </div>
            </div>
        </>
    )
}

export default ImageBackgroundTextSlideLayout 