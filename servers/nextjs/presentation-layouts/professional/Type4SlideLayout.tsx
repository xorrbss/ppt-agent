import React from 'react'
import * as z from "zod";

export const layoutId = 'type4-slide'
export const layoutName = 'Type4 Slide'
export const layoutDescription = 'A chart-focused layout with title, chart visualization, and description text.'

const type4SlideSchema = z.object({
    title: z.string().min(3).max(100).default('Chart Analysis').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(500).default('This chart shows important data trends and insights that help understand the current situation and make informed decisions.').meta({
        description: "Description text for the chart",
    }),
    chartData: z.any().optional().meta({
        description: "Chart data object",
    }),
    isFullSizeChart: z.boolean().default(false).meta({
        description: "Whether to display chart in full size mode",
    })
})

export const Schema = type4SlideSchema

export type Type4SlideData = z.infer<typeof type4SlideSchema>

interface Type4SlideLayoutProps {
    data?: Partial<Type4SlideData>
}

const Type4SlideLayout: React.FC<Type4SlideLayoutProps> = ({ data: slideData }) => {
    const isFullSizeGraph = slideData?.isFullSizeChart || false

    // Simple placeholder chart component
    const ChartPlaceholder = () => (
        <div className="w-full h-64 lg:h-80 bg-gradient-to-br from-blue-50 to-blue-100 rounded-lg border-2 border-blue-200 flex items-center justify-center">
            <div className="text-center">
                <div className="text-blue-600 text-4xl mb-4">📊</div>
                <p className="text-blue-700 font-semibold">Chart Component</p>
                <p className="text-blue-600 text-sm mt-1">Data visualization will appear here</p>
            </div>
        </div>
    )

    return (
        <div
            className=" rounded-sm w-full max-w-[1280px] px-3 py-[10px] sm:px-12 lg:px-20 sm:py-[40px] lg:py-[86px] shadow-lg max-h-[720px] flex flex-col items-center justify-center aspect-video bg-white relative z-20 mx-auto"

        >
            <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight mb-4 lg:mb-8">
                {slideData?.title || 'Chart Analysis'}
            </h1>

            <div className={`flex w-full  items-center ${isFullSizeGraph
                ? "flex-col mt-4 lg:mt-10 gap-2 sm:gap-4 md:gap-6 lg:gap-10"
                : "mt-4 lg:mt-16 gap-4 sm:gap-8 md:gap-12 lg:gap-16"
                }`}>
                <div className="w-full">
                    <ChartPlaceholder />
                </div>
                <div className="w-full text-center">
                    <p className={`text-base sm:text-lg lg:text-xl text-gray-700 leading-relaxed ${isFullSizeGraph ? 'text-center' : ''}`}>
                        {slideData?.description || 'This chart shows important data trends and insights that help understand the current situation and make informed decisions.'}
                    </p>
                </div>
            </div>
        </div>
    )
}

export default Type4SlideLayout 