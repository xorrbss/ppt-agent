import React from 'react'
import { z } from "zod";

const conclusionSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Conclusion'),
    subtitle: z.string().min(3).max(150).optional().describe("Optional subtitle or closing message"),
    keyTakeaways: z.array(z.string().min(5).max(200)).min(1).max(5).default([
        'First key takeaway',
        'Second important point',
        'Third crucial insight'
    ]).describe("List of key takeaways (1-5 items)"),
    callToAction: z.string().min(5).max(200).optional().describe("Call to action or next steps"),
    contactInfo: z.object({
        email: z.string().email().optional(),
        phone: z.string().optional(),
        website: z.string().url().optional(),
        linkedin: z.string().url().optional()
    }).optional(),
    backgroundImage: z.string().url().optional().default("https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4").describe("URL to background image for the slide")
})

// Standardized schema export
export const Schema = conclusionSlideSchema

export type ConclusionSlideData = z.infer<typeof conclusionSlideSchema>

interface ConclusionSlideLayoutProps {
    data?: Partial<ConclusionSlideData>
}

const ConclusionSlideLayout: React.FC<ConclusionSlideLayoutProps> = ({ data }) => {
    const slideData = conclusionSlideSchema.parse(data || {})

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col justify-center items-center bg-gradient-to-br from-blue-50 via-white to-blue-100 overflow-hidden"
            style={slideData.backgroundImage ? {
                backgroundImage: `url(${slideData.backgroundImage})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >
            {slideData.backgroundImage && (
                <div className="absolute inset-0 bg-black bg-opacity-50" />
            )}

            <div className="relative z-10 text-center max-w-6xl mx-auto px-16 py-12">
                {/* Main Title */}
                <h1 className={`text-6xl font-bold mb-6 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                    {slideData.title}
                </h1>

                {/* Subtitle */}
                {slideData.subtitle && (
                    <p className={`text-3xl mb-12 font-light ${slideData.backgroundImage ? 'text-gray-200' : 'text-gray-600'}`}>
                        {slideData.subtitle}
                    </p>
                )}

                {/* Key Takeaways */}
                <div className="mb-12">
                    <h2 className={`text-3xl font-semibold mb-8 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                        Key Takeaways
                    </h2>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {slideData.keyTakeaways.map((takeaway, index) => (
                            <div key={index} className="bg-white bg-opacity-90 rounded-lg p-6 shadow-lg hover:shadow-xl transition-shadow">
                                <div className="flex items-center mb-4">
                                    <div className="w-10 h-10 bg-blue-600 text-white rounded-full flex items-center justify-center font-bold text-lg mr-4">
                                        {index + 1}
                                    </div>
                                </div>
                                <p className="text-lg text-gray-800 leading-relaxed">
                                    {takeaway}
                                </p>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Call to Action */}
                {slideData.callToAction && (
                    <div className="mb-12">
                        <div className="bg-blue-600 text-white rounded-lg p-8 inline-block shadow-lg hover:bg-blue-700 transition-colors">
                            <p className="text-2xl font-semibold">
                                {slideData.callToAction}
                            </p>
                        </div>
                    </div>
                )}

                {/* Contact Information */}
                {slideData.contactInfo && (
                    <div className="mt-12">
                        <h3 className={`text-2xl font-semibold mb-6 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>
                            Get in Touch
                        </h3>
                        <div className="flex flex-wrap justify-center gap-8 text-lg">
                            {slideData.contactInfo.email && (
                                <div className="flex items-center">
                                    <span className={`font-semibold mr-2 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>Email:</span>
                                    <span className={slideData.backgroundImage ? 'text-gray-200' : 'text-gray-700'}>{slideData.contactInfo.email}</span>
                                </div>
                            )}
                            {slideData.contactInfo.phone && (
                                <div className="flex items-center">
                                    <span className={`font-semibold mr-2 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>Phone:</span>
                                    <span className={slideData.backgroundImage ? 'text-gray-200' : 'text-gray-700'}>{slideData.contactInfo.phone}</span>
                                </div>
                            )}
                            {slideData.contactInfo.website && (
                                <div className="flex items-center">
                                    <span className={`font-semibold mr-2 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>Website:</span>
                                    <span className={slideData.backgroundImage ? 'text-gray-200' : 'text-gray-700'}>{slideData.contactInfo.website}</span>
                                </div>
                            )}
                            {slideData.contactInfo.linkedin && (
                                <div className="flex items-center">
                                    <span className={`font-semibold mr-2 ${slideData.backgroundImage ? 'text-white' : 'text-gray-900'}`}>LinkedIn:</span>
                                    <span className={slideData.backgroundImage ? 'text-gray-200' : 'text-gray-700'}>{slideData.contactInfo.linkedin}</span>
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>

            {/* Decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-gradient-to-r from-blue-500 to-blue-700" />
        </div>
    )
}

export default ConclusionSlideLayout 