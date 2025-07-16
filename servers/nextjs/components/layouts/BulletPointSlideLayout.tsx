import React from 'react'
import * as z from "zod";
import { ImageSchema } from './defaultSchemes';

export const layoutId = 'bullet-point-slide'
export const layoutName = 'Bullet Point Slide'
export const layoutDescription = 'A slide with a title, subtitle, and a list of bullet points.'


const bulletPointSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Points').meta({
        description: "Title of the slide",

    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or description",
    }),
    icon: z.string().optional().meta({
        description: "Icon to display in the slide",
    }),
    bulletPoints: z.array(z.string().min(5).max(200)).min(2).max(8).default([
        'First key point that highlights important information',
        'Second bullet point with valuable insights',
        'Third point demonstrating clear benefits',
        'Fourth item showcasing key features'
    ]).meta({
        description: "List of bullet points (2-8 items)",
    }),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    }),
})
export const Schema = bulletPointSlideSchema


export type BulletPointSlideData = z.infer<typeof bulletPointSlideSchema>

interface BulletPointSlideLayoutProps {
    data?: Partial<BulletPointSlideData>
}

const BulletPointSlideLayout: React.FC<BulletPointSlideLayoutProps> = ({ data: slideData }) => {


    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >



            {/* Content */}
            <div className="relative z-10 h-full flex flex-col px-16 py-12">
                {/* Header */}
                <div className="text-center mb-12">
                    <h1 className="text-6xl font-black mb-8 leading-tight tracking-tight max-w-4xl mx-auto">
                        <span className="text-gray-900">{slideData?.title?.split(' ').slice(0, -1).join(' ')}</span>{' '}
                        <span className={`bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent`}>
                            {slideData?.title?.split(' ').slice(-1)[0]}
                        </span>
                    </h1>

                    {/* Subtitle */}
                    {slideData?.subtitle && (
                        <div className="relative mb-8">
                            <div className="relative flex justify-center">
                                <div className={`w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full shadow-lg`} />
                                <div className={`absolute inset-0 w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full blur-sm opacity-50`} />
                            </div>
                            <p className="text-xl text-gray-600 font-light mt-6 max-w-2xl mx-auto leading-relaxed">
                                {slideData.subtitle}
                            </p>
                        </div>
                    )}

                    {/* Corner accent */}
                    <div className={`absolute top-0 right-0 w-24 h-24 bg-gradient-to-bl from-blue-600 to-blue-800 opacity-5 rounded-bl-full`} />
                </div>

                {/* Bullet Points */}
                <div className="flex-1 max-w-4xl mx-auto">
                    <div className="grid gap-6">
                        {slideData?.bulletPoints?.map((point, index) => (
                            <div key={index} className="group flex items-start gap-6 p-6 rounded-2xl bg-white/80 backdrop-blur-sm hover:bg-white/90 transition-all duration-200 hover:shadow-lg">
                                <div className="relative flex-shrink-0 mt-1">
                                    <div className={`bg-blue-600 w-4 h-4 rounded-full shadow-lg group-hover:scale-125 transition-all duration-200 relative z-10`} />
                                    <div className={`absolute inset-0 bg-blue-600 w-4 h-4 rounded-full blur-sm opacity-50 group-hover:opacity-75 transition-opacity duration-200`} />
                                </div>
                                <p className="text-lg text-gray-800 leading-relaxed font-medium">
                                    {point}
                                </p>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* Bottom accent */}
            <div className="absolute bottom-0 left-0 right-0">
                <div className={`absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r from-blue-600 to-blue-800 shadow-lg`}>
                    {/* Glow effect */}
                    <div className={`absolute inset-0 bg-gradient-to-r from-blue-600 to-blue-800 blur-sm opacity-50`} />
                </div>
                {/* Corner accents */}
                <div className={`absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-blue-600 to-blue-800 opacity-5 rounded-bl-full`} />
            </div>
        </div>
    )
}

export default BulletPointSlideLayout 