import React from 'react'
import * as z from "zod";

export const layoutId = 'timeline-trend-slide'
export const layoutName = 'Timeline / Trend Slide'
export const layoutDescription = 'A layout for displaying time-based milestones with connecting lines and responsive timeline visualization.'

const timelineItemSchema = z.object({
    title: z.string().min(2).max(50).meta({ description: "Milestone title" }),
    date: z.string().min(2).max(30).meta({ description: "Date or time period" }),
    status: z.enum(['completed', 'current', 'upcoming', 'delayed']).default('upcoming').meta({ description: "Milestone status" }),
    icon: z.string().optional().meta({ description: "Icon identifier (optional)" }),
    description: z.string().optional().meta({ description: "Optional description" }),
});

const timelineTrendSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Project Timeline & Milestones').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(300).default('This timeline tracks key project milestones and deliverables, providing a clear view of progress and upcoming objectives across the project lifecycle.').meta({
        description: "Bottom description text",
    }),
    timeline: z.array(timelineItemSchema).min(3).max(8).default([
        {
            title: 'Project Kickoff',
            date: 'Jan 2024',
            status: 'completed',
            description: 'Initial planning and team setup'
        },
        {
            title: 'Research Phase',
            date: 'Feb 2024',
            status: 'completed',
            description: 'Market research and requirements gathering'
        },
        {
            title: 'Design Phase',
            date: 'Mar 2024',
            status: 'completed',
            description: 'UI/UX design and prototyping'
        },
        {
            title: 'Development',
            date: 'Apr-Jun 2024',
            status: 'current',
            description: 'Core development and testing'
        },
        {
            title: 'Beta Testing',
            date: 'Jul 2024',
            status: 'upcoming',
            description: 'User testing and feedback collection'
        },
        {
            title: 'Launch',
            date: 'Aug 2024',
            status: 'upcoming',
            description: 'Product launch and go-to-market'
        },
    ]).meta({
        description: "Timeline milestone items (3-8 items)",
    }),
    showConnectingLines: z.boolean().default(true).meta({
        description: "Whether to show connecting lines between milestones",
    }),
    lineStyle: z.enum(['solid', 'dotted', 'dashed']).default('solid').meta({
        description: "Style of connecting lines",
    }),
})

export const Schema = timelineTrendSlideSchema

export type TimelineTrendSlideData = z.infer<typeof timelineTrendSlideSchema>

interface TimelineTrendSlideLayoutProps {
    data?: Partial<TimelineTrendSlideData>
}

const TimelineTrendSlideLayout: React.FC<TimelineTrendSlideLayoutProps> = ({ data: slideData }) => {
    const timeline = slideData?.timeline || [];
    const showConnectingLines = slideData?.showConnectingLines ?? true;
    const lineStyle = slideData?.lineStyle || 'solid';

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'completed':
                return 'bg-green-500 border-green-500';
            case 'current':
                return 'bg-blue-500 border-blue-500';
            case 'upcoming':
                return 'bg-gray-300 border-gray-300';
            case 'delayed':
                return 'bg-red-500 border-red-500';
            default:
                return 'bg-gray-300 border-gray-300';
        }
    };

    const getTextColor = (status: string) => {
        switch (status) {
            case 'completed':
                return 'text-green-600';
            case 'current':
                return 'text-blue-600';
            case 'upcoming':
                return 'text-gray-600';
            case 'delayed':
                return 'text-red-600';
            default:
                return 'text-gray-600';
        }
    };

    const getLineStyle = () => {
        switch (lineStyle) {
            case 'dotted':
                return 'border-dotted';
            case 'dashed':
                return 'border-dashed';
            default:
                return 'border-solid';
        }
    };

    const renderIcon = (status: string) => {
        switch (status) {
            case 'completed':
                return (
                    <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                );
            case 'current':
                return (
                    <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clipRule="evenodd" />
                    </svg>
                );
            case 'delayed':
                return (
                    <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                    </svg>
                );
            default:
                return null;
        }
    };

    return (
        <>
            {/* Import Google Fonts */}
            <link
                href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Nunito:wght@400;500;600;700&display=swap"
                rel="stylesheet"
            />

            <div
                className="w-full rounded-sm max-w-[1280px] shadow-md h-[720px] flex flex-col aspect-video bg-white relative z-20 mx-auto overflow-hidden"
                style={{
                    fontFamily: 'Nunito, sans-serif'
                }}
            >
                {/* Glass overlay background */}
                <div className="absolute inset-0 bg-white/20 backdrop-blur-sm rounded-sm border border-slate-200"></div>

                <div className="relative z-10 flex flex-col w-full h-full p-4 sm:p-6 lg:p-8">
                    {/* Header section */}
                    <div className="flex-shrink-0 h-12 sm:h-16 flex items-center justify-center">
                        <h1
                            className="text-3xl font-bold text-gray-900 leading-tight text-center"
                            style={{
                                fontFamily: 'Space Grotesk, sans-serif'
                            }}
                        >
                            {slideData?.title || 'Project Timeline & Milestones'}
                        </h1>
                    </div>

                    {/* Timeline section */}
                    <div className="flex-1 w-full overflow-hidden">
                        <div className="h-full flex items-center justify-center">
                            <div className="w-full max-w-6xl">
                                {/* Timeline container */}
                                <div className="relative">
                                    {/* Timeline items */}
                                    <div className="flex justify-between items-center relative">
                                        {timeline.map((item, index) => (
                                            <div key={index} className="flex flex-col items-center relative z-10">
                                                {/* Circle/Icon */}
                                                <div className={`w-12 h-12 rounded-full border-4 ${getStatusColor(item.status)} flex items-center justify-center shadow-lg bg-white/10 backdrop-blur-sm`}>
                                                    {renderIcon(item.status)}
                                                </div>

                                                {/* Label */}
                                                <div className="mt-3 text-center max-w-20">
                                                    <div
                                                        className={`text-xs font-semibold ${getTextColor(item.status)} mb-1`}
                                                        style={{
                                                            fontFamily: 'Space Grotesk, sans-serif'
                                                        }}
                                                    >
                                                        {item.title}
                                                    </div>
                                                    <div
                                                        className="text-xs text-gray-600"
                                                        style={{
                                                            fontFamily: 'Nunito, sans-serif'
                                                        }}
                                                    >
                                                        {item.date}
                                                    </div>
                                                    {item.description && (
                                                        <div
                                                            className="text-xs text-gray-500 mt-1 leading-tight"
                                                            style={{
                                                                fontFamily: 'Nunito, sans-serif'
                                                            }}
                                                        >
                                                            {item.description}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        ))}

                                        {/* Connecting line */}
                                        {showConnectingLines && (
                                            <div className={`absolute top-6 left-6 right-6 h-0 border-t-2 border-gray-400 ${getLineStyle()} -z-10`} />
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Bottom Description section */}
                    <div className="flex-shrink-0 h-16 sm:h-20 flex items-center justify-center">
                        <p
                            className="text-sm sm:text-base text-center text-gray-700 leading-relaxed max-w-4xl px-4"
                            style={{
                                fontFamily: 'Nunito, sans-serif'
                            }}
                        >
                            {slideData?.description || 'This timeline tracks key project milestones and deliverables, providing a clear view of progress and upcoming objectives across the project lifecycle.'}
                        </p>
                    </div>
                </div>
            </div>
        </>
    )
}

export default TimelineTrendSlideLayout 