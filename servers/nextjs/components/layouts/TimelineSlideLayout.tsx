import React from 'react'
import * as z from "zod";
import { ImageSchema, IconSchema } from './defaultSchemes';

export const layoutId = 'timeline-slide'
export const layoutName = 'Timeline Slide'
export const layoutDescription = 'A professional slide featuring a chronological timeline with dates, events, and descriptions.'

const timelineSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Our Journey').meta({
        description: "Main title of the slide",
    }),
    subtitle: z.string().min(10).max(200).optional().meta({
        description: "Optional subtitle or description",
    }),
    events: z.array(z.object({
        date: z.string().min(2).max(20).meta({
            description: "Date or time period",
        }),
        title: z.string().min(2).max(50).meta({
            description: "Event title",
        }),
        description: z.string().min(10).max(150).meta({
            description: "Event description",
        }),
        icon: IconSchema.default({
            url: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
            prompt: 'Default event icon'
        }).meta({
            description: "Icon for the event",
        })
    })).min(2).max(6).default([
        {
            date: '2020',
            title: 'Foundation',
            description: 'Company founded with a vision to transform digital experiences',
            icon: {
                url: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                prompt: 'Foundation icon'
            }
        },
        {
            date: '2021',
            title: 'First Success',
            description: 'Launched first product and gained initial market traction',
            icon: {
                url: 'https://cdn.pixabay.com/photo/2016/02/19/11/19/office-1209640_1280.jpg',
                prompt: 'First success icon'
            }
        },
        {
            date: '2022',
            title: 'Expansion',
            description: 'Expanded team and entered new markets with innovative solutions',
            icon: {
                url: 'https://cdn.pixabay.com/photo/2017/08/10/08/47/laptop-2619235_1280.jpg',
                prompt: 'Expansion icon'
            }
        },
        {
            date: '2023',
            title: 'Innovation',
            description: 'Introduced breakthrough technology and achieved industry recognition',
            icon: {
                url: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                prompt: 'Innovation icon'
            }
        }
    ]).meta({
        description: "List of timeline events (2-6 items)",
    }),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    })
})

export const Schema = timelineSlideSchema

export type TimelineSlideData = z.infer<typeof timelineSlideSchema>

interface TimelineSlideLayoutProps {
    data?: Partial<TimelineSlideData>
}

const TimelineSlideLayout: React.FC<TimelineSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="relative w-full aspect-[16/9] flex flex-col bg-gradient-to-br from-slate-50 via-white to-slate-100 overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300"
            style={slideData?.backgroundImage ? {
                backgroundImage: `linear-gradient(135deg, rgba(0,0,0,0.5), rgba(0,0,0,0.7)), url(${slideData.backgroundImage.url})`,
                backgroundSize: 'cover',
                backgroundPosition: 'center'
            } : {}}
        >
            {/* Background Effects */}
            <div className="absolute inset-0 overflow-hidden pointer-events-none">
                <div className="absolute top-0 right-0 w-96 h-96 bg-blue-600 rounded-full transform translate-x-32 -translate-y-32 blur-3xl opacity-10" />
                <div className="absolute bottom-0 left-0 w-64 h-64 bg-blue-600 rounded-full transform -translate-x-16 translate-y-16 blur-2xl opacity-10" />
            </div>

            {/* Header section */}
            <div className="text-center px-12 py-6 print:px-8 print:py-4 relative z-10">
                <h1 className="text-4xl font-bold text-blue-600 mb-4 leading-tight print:text-3xl">
                    {slideData?.title || 'Our Journey'}
                </h1>
                {slideData?.subtitle && (
                    <p className="text-lg text-gray-600 max-w-2xl mx-auto leading-relaxed print:text-base">
                        {slideData.subtitle}
                    </p>
                )}
                <div className="mt-4 w-24 h-1 bg-gradient-to-r from-blue-600 to-blue-800 mx-auto rounded-full"></div>
            </div>

            {/* Timeline section */}
            <div className="flex-1 px-12 pb-8 print:px-8 print:pb-6 relative z-10">
                <div className="relative max-w-6xl mx-auto">
                    {/* Timeline line */}
                    <div className="absolute left-1/2 transform -translate-x-1/2 w-1 h-full bg-gradient-to-b from-blue-600 to-blue-800 rounded-full"></div>

                    {/* Timeline events */}
                    <div className="space-y-8 print:space-y-6">
                        {slideData?.events?.map((event, index) => (
                            <div
                                key={index}
                                className={`flex items-center ${index % 2 === 0 ? 'justify-start' : 'justify-end'} relative`}
                            >
                                {/* Event content */}
                                <div className={`w-5/12 ${index % 2 === 0 ? 'text-right pr-8' : 'text-left pl-8'} print:w-2/5`}>
                                    <div className="bg-white/95 backdrop-blur-sm rounded-2xl p-6 shadow-xl border border-white/50 group hover:transform hover:scale-105 transition-all duration-300 print:shadow-md print:p-4">
                                        {/* Date */}
                                        <div className="text-blue-600 font-bold text-lg mb-2 print:text-base">
                                            {event.date}
                                        </div>

                                        {/* Title with icon */}
                                        <h3 className="text-xl font-bold text-gray-800 mb-3 leading-tight flex items-center gap-2 print:text-lg">
                                            <div className="w-8 h-8 bg-blue-100 rounded-lg flex items-center justify-center overflow-hidden flex-shrink-0 print:w-6 print:h-6">
                                                <img
                                                    src={event.icon?.url || ''}
                                                    alt={event.icon?.prompt || event.title}
                                                    className="w-full h-full object-cover"
                                                />
                                            </div>
                                            {event.title}
                                        </h3>

                                        {/* Description */}
                                        <p className="text-gray-700 leading-relaxed text-sm print:text-xs">
                                            {event.description}
                                        </p>

                                        {/* Accent */}
                                        <div className={`absolute top-0 ${index % 2 === 0 ? 'left-0' : 'right-0'} ${index % 2 === 0 ? 'right-0' : 'left-0'} h-1 bg-gradient-to-r from-blue-600 to-blue-800`} />
                                    </div>
                                </div>

                                {/* Timeline dot */}
                                <div className="absolute left-1/2 transform -translate-x-1/2 w-6 h-6 bg-gradient-to-br from-blue-600 to-blue-800 rounded-full border-4 border-white shadow-xl z-10 print:w-4 print:h-4 print:border-2">
                                    <div className="absolute inset-0 bg-gradient-to-br from-blue-600 to-blue-800 rounded-full animate-pulse opacity-50"></div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* Bottom decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800"></div>
        </div>
    )
}

export default TimelineSlideLayout 