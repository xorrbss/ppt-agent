import React from 'react'
import * as z from "zod";
import { ImageSchema, IconSchema } from './defaultSchemes';

export const layoutId = 'card-slide'
export const layoutName = 'Card Slide'
export const layoutDescription = 'A professional slide featuring feature cards with icons, titles, and descriptions.'

const cardSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Our Features').meta({
        description: "Main title of the slide",
    }),
    subtitle: z.string().min(10).max(200).optional().meta({
        description: "Optional subtitle or description",
    }),
    cards: z.array(z.object({
        icon: IconSchema.default({
            url: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
            prompt: 'Default card icon'
        }).meta({
            description: "Icon for the card",
        }),
        title: z.string().min(2).max(50).meta({
            description: "Title for the card",
        }),
        description: z.string().min(10).max(150).meta({
            description: "Description of the feature",
        })
    })).min(2).max(6).default([
        {
            icon: {
                url: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                prompt: 'Lightning fast icon'
            },
            title: 'Lightning Fast',
            description: 'Optimized performance for quick results and seamless user experience'
        },
        {
            icon: {
                url: 'https://cdn.pixabay.com/photo/2016/02/19/11/19/office-1209640_1280.jpg',
                prompt: 'Secure and safe icon'
            },
            title: 'Secure & Safe',
            description: 'Enterprise-grade security with advanced encryption and protection'
        },
        {
            icon: {
                url: 'https://cdn.pixabay.com/photo/2017/08/10/08/47/laptop-2619235_1280.jpg',
                prompt: 'Precise targeting icon'
            },
            title: 'Precise Targeting',
            description: 'Advanced analytics to reach your exact audience with precision'
        }
    ]).meta({
        description: "List of feature cards (2-6 items)",
    }),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = cardSlideSchema

export type CardSlideData = z.infer<typeof cardSlideSchema>

interface CardSlideLayoutProps {
    data?: Partial<CardSlideData>
}

const CardSlideLayout: React.FC<CardSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData.backgroundImage.url})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >


            {/* Header section */}
            <div className="text-center px-12 py-8 print:px-8 print:py-6 relative z-10">
                <h1 className="text-4xl font-bold text-blue-600 mb-4 leading-tight print:text-3xl">
                    {slideData?.title || 'Our Features'}
                </h1>
                {slideData?.subtitle && (
                    <p className="text-lg text-gray-600 max-w-2xl mx-auto leading-relaxed print:text-base">
                        {slideData.subtitle}
                    </p>
                )}
                <div className="mt-4 w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 mx-auto rounded-full"></div>
            </div>

            {/* Cards section */}
            <div className="flex-1 px-12 pb-8 print:px-8 print:pb-6 relative z-10">
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 h-full items-center">
                    {slideData?.cards?.map((card, index) => (
                        <div
                            key={index}
                            className="relative bg-white/95 backdrop-blur-sm rounded-2xl p-6 shadow-2xl border border-white/50 text-center group hover:transform hover:scale-105 transition-all duration-300 print:shadow-md print:p-4"
                        >
                            {/* Card accent */}
                            <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800" />

                            {/* Icon */}
                            <div className="mb-4 group-hover:scale-110 transition-transform duration-300">
                                <div className="w-16 h-16 mx-auto bg-blue-100 rounded-xl flex items-center justify-center overflow-hidden print:w-12 print:h-12">
                                    <img
                                        src={card.icon?.url || ''}
                                        alt={card.icon?.prompt || card.title}
                                        className="w-full h-full object-cover"
                                    />
                                </div>
                            </div>

                            {/* Title */}
                            <h3 className="text-xl font-bold text-blue-600 mb-3 leading-tight print:text-lg">
                                {card.title}
                            </h3>

                            {/* Description */}
                            <p className="text-gray-700 leading-relaxed text-sm print:text-xs">
                                {card.description}
                            </p>

                            {/* Background decoration */}
                            <div className="absolute bottom-0 right-0 w-16 h-16 bg-gradient-to-tl from-blue-600 to-blue-800 opacity-5 rounded-tl-full" />
                        </div>
                    ))}
                </div>
            </div>

            {/* Bottom decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800"></div>
        </div>
    )
}

export default CardSlideLayout 