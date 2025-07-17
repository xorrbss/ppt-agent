import React from 'react'
import * as z from "zod";
import { ImageSchema, IconSchema } from './defaultSchemes';

export const layoutId = 'process-slide'
export const layoutName = 'Process Slide'
export const layoutDescription = 'A professional slide featuring step-by-step processes with icons, titles, and descriptions.'

const processSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Our Process').meta({
        description: "Main title of the slide",
    }),
    subtitle: z.string().min(10).max(200).optional().meta({
        description: "Optional subtitle or description",
    }),
    steps: z.array(z.object({
        icon: IconSchema.default({
            __icon_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
            __icon_query__: 'Default step icon'
        }).meta({
            description: "Icon for the step",
        }),
        title: z.string().min(2).max(50).meta({
            description: "Title for the step",
        }),
        description: z.string().min(10).max(150).meta({
            description: "Description of the step",
        })
    })).min(2).max(6).default([
        {
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                __icon_query__: 'Plan and strategy icon'
            },
            title: 'Plan & Strategy',
            description: 'Define objectives, analyze requirements, and create a comprehensive roadmap'
        },
        {
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2016/02/19/11/19/office-1209640_1280.jpg',
                __icon_query__: 'Execute and build icon'
            },
            title: 'Execute & Build',
            description: 'Implement solutions with precision using cutting-edge technology and best practices'
        },
        {
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2017/08/10/08/47/laptop-2619235_1280.jpg',
                __icon_query__: 'Launch and optimize icon'
            },
            title: 'Launch & Optimize',
            description: 'Deploy the solution and continuously improve based on performance metrics'
        }
    ]).meta({
        description: "List of process steps (2-6 items)",
    }),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = processSlideSchema

export type ProcessSlideData = z.infer<typeof processSlideSchema>

interface ProcessSlideLayoutProps {
    data?: Partial<ProcessSlideData>
}

const ProcessSlideLayout: React.FC<ProcessSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData.backgroundImage.__image_url__})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >


            {/* Header section */}
            <div className="text-center px-12 py-8 print:px-8 print:py-6 relative z-10">
                <h1 className="text-4xl font-bold text-blue-600 mb-4 leading-tight print:text-3xl">
                    {slideData?.title || 'Our Process'}
                </h1>
                {slideData?.subtitle && (
                    <p className="text-lg text-gray-600 max-w-2xl mx-auto leading-relaxed print:text-base">
                        {slideData.subtitle}
                    </p>
                )}
                <div className="mt-4 w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 mx-auto rounded-full"></div>
            </div>

            {/* Process steps section */}
            <div className="flex-1 px-12 pb-8 print:px-8 print:pb-6 relative z-10">
                <div className="flex justify-center items-center h-full">
                    <div className="flex flex-wrap justify-center items-center gap-8 max-w-6xl">
                        {slideData?.steps?.map((step, index) => (
                            <React.Fragment key={index}>
                                {/* Process Step */}
                                <div className="flex flex-col items-center text-center max-w-xs group">
                                    {/* Step Number and Icon */}
                                    <div className="relative mb-4">
                                        <div className="w-20 h-20 bg-gradient-to-br from-blue-600 to-blue-800 rounded-full flex items-center justify-center shadow-2xl group-hover:scale-110 transition-transform duration-300 print:w-16 print:h-16">
                                            <span className="text-white font-bold text-lg print:text-base">
                                                {index + 1}
                                            </span>
                                        </div>
                                        <div className="absolute -bottom-2 -right-2 w-8 h-8 bg-white rounded-full flex items-center justify-center shadow-lg print:w-6 print:h-6">
                                            <img
                                                src={step.icon?.__icon_url__ || ''}
                                                alt={step.icon?.__icon_query__ || step.title}
                                                className="w-6 h-6 object-cover rounded-full print:w-4 print:h-4"
                                            />
                                        </div>
                                    </div>

                                    {/* Step Title */}
                                    <h3 className="text-xl font-bold text-blue-600 mb-3 leading-tight print:text-lg">
                                        {step.title}
                                    </h3>

                                    {/* Step Description */}
                                    <p className="text-gray-700 leading-relaxed text-sm print:text-xs">
                                        {step.description}
                                    </p>
                                </div>

                                {/* Arrow between steps */}
                                {index < (slideData?.steps?.length || 0) - 1 && (
                                    <div className="hidden lg:flex items-center">
                                        <div className="w-12 h-0.5 bg-gradient-to-r from-blue-600 to-blue-800 relative print:w-8">
                                            <div className="absolute right-0 top-1/2 transform -translate-y-1/2 w-0 h-0 border-l-4 border-l-blue-600 border-t-2 border-b-2 border-t-transparent border-b-transparent"></div>
                                        </div>
                                    </div>
                                )}
                            </React.Fragment>
                        ))}
                    </div>
                </div>
            </div>

            {/* Bottom decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800"></div>
        </div>
    )
}

export default ProcessSlideLayout 