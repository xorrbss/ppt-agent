import React from 'react'
import * as z from "zod";

export const layoutId = 'kpi-summary-grid-slide'
export const layoutName = 'KPI Summary Grid Slide'
export const layoutDescription = 'A layout for displaying key performance indicators with big numbers, labels, and trend indicators in a grid format.'

const kpiItemSchema = z.object({
    value: z.string().min(1).max(20).meta({ description: "KPI value (e.g., '$2.4M', '95%', '1,234')" }),
    label: z.string().min(2).max(50).meta({ description: "KPI label" }),
    trend: z.enum(['up', 'down', 'flat', 'none']).default('none').meta({ description: "Trend direction" }),
    trendValue: z.string().optional().meta({ description: "Trend percentage or value" }),
    sparklineData: z.array(z.number()).optional().meta({ description: "Mini sparkline data points" }),
});

const kpiSummaryGridSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Key Performance Indicators').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(300).default('These key metrics provide a comprehensive overview of business performance, tracking essential indicators that drive strategic decision-making and operational excellence.').meta({
        description: "Bottom description text",
    }),
    kpis: z.array(kpiItemSchema).min(2).max(6).default([
        {
            value: '$2.4M',
            label: 'Total Revenue',
            trend: 'up',
            trendValue: '+12.5%',
            sparklineData: [10, 15, 12, 18, 22, 25, 28, 24, 30, 35]
        },
        {
            value: '18.3%',
            label: 'Growth Rate',
            trend: 'up',
            trendValue: '+3.2%',
            sparklineData: [8, 12, 10, 15, 18, 20, 22, 19, 25, 28]
        },
        {
            value: '24.7%',
            label: 'Conversion Rate',
            trend: 'down',
            trendValue: '-1.8%',
            sparklineData: [30, 28, 25, 22, 26, 24, 20, 18, 22, 25]
        },
        {
            value: '1,234',
            label: 'Active Users',
            trend: 'up',
            trendValue: '+8.9%',
            sparklineData: [100, 120, 110, 140, 160, 180, 170, 200, 220, 240]
        },
        {
            value: '95.2%',
            label: 'Customer Satisfaction',
            trend: 'flat',
            trendValue: '+0.1%',
            sparklineData: [90, 92, 91, 93, 94, 95, 94, 96, 95, 95]
        },
        {
            value: '47s',
            label: 'Avg Response Time',
            trend: 'down',
            trendValue: '-12.3%',
            sparklineData: [60, 58, 55, 52, 50, 48, 45, 47, 46, 47]
        },
    ]).meta({
        description: "Array of KPI items (2-6 items)",
    }),
})

export const Schema = kpiSummaryGridSlideSchema

export type KPISummaryGridSlideData = z.infer<typeof kpiSummaryGridSlideSchema>

interface KPISummaryGridSlideLayoutProps {
    data?: Partial<KPISummaryGridSlideData>
}

const KPISummaryGridSlideLayout: React.FC<KPISummaryGridSlideLayoutProps> = ({ data: slideData }) => {
    const kpis = slideData?.kpis || [];

    // Determine grid layout based on KPI count
    const getGridLayout = (count: number) => {
        switch (count) {
            case 2:
                return 'grid-cols-2';
            case 3:
                return 'grid-cols-3';
            case 4:
                return 'grid-cols-2 md:grid-cols-4';
            case 5:
            case 6:
                return 'grid-cols-2 md:grid-cols-3';
            default:
                return 'grid-cols-3';
        }
    };

    // Render trend arrow
    const renderTrendArrow = (trend: string) => {
        switch (trend) {
            case 'up':
                return (
                    <svg className="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M3.293 9.707a1 1 0 010-1.414l6-6a1 1 0 011.414 0l6 6a1 1 0 01-1.414 1.414L11 5.414V17a1 1 0 11-2 0V5.414L4.707 9.707a1 1 0 01-1.414 0z" clipRule="evenodd" />
                    </svg>
                );
            case 'down':
                return (
                    <svg className="w-4 h-4 text-red-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 10.293a1 1 0 010 1.414l-6 6a1 1 0 01-1.414 0l-6-6a1 1 0 111.414-1.414L9 14.586V3a1 1 0 012 0v11.586l4.293-4.293a1 1 0 011.414 0z" clipRule="evenodd" />
                    </svg>
                );
            case 'flat':
                return (
                    <svg className="w-4 h-4 text-gray-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M3 10a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clipRule="evenodd" />
                    </svg>
                );
            default:
                return null;
        }
    };

    // Render mini sparkline
    const renderSparkline = (data: number[] | undefined) => {
        if (!data || data.length === 0) return null;

        const max = Math.max(...data);
        const min = Math.min(...data);
        const range = max - min;
        
        const points = data.map((value, index) => {
            const x = (index / (data.length - 1)) * 80;
            const y = 20 - ((value - min) / range) * 20;
            return `${x},${y}`;
        }).join(' ');

        return (
            <svg className="w-20 h-5" viewBox="0 0 80 20">
                <polyline
                    points={points}
                    fill="none"
                    stroke="#3b82f6"
                    strokeWidth="1.5"
                    className="opacity-70"
                />
            </svg>
        );
    };

    const getTrendColor = (trend: string) => {
        switch (trend) {
            case 'up':
                return 'text-green-600';
            case 'down':
                return 'text-red-600';
            case 'flat':
                return 'text-gray-600';
            default:
                return 'text-gray-600';
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
                className="w-full rounded-sm max-w-[1280px] shadow-md h-[720px] flex flex-col aspect-video bg-stone-100 relative z-20 mx-auto overflow-hidden"
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
                            {slideData?.title || 'Key Performance Indicators'}
                        </h1>
                    </div>

                    {/* KPI Grid section */}
                    <div className="flex-1 w-full overflow-hidden">
                        <div className={`grid ${getGridLayout(kpis.length)} gap-6 h-full content-center`}>
                            {kpis.map((kpi, index) => (
                                <div key={index} className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-4 sm:p-6 flex flex-col items-center justify-center text-center">
                                    {/* Big Number */}
                                    <div 
                                        className="text-3xl sm:text-4xl lg:text-5xl font-bold text-gray-900 mb-2"
                                        style={{
                                            fontFamily: 'Space Grotesk, sans-serif'
                                        }}
                                    >
                                        {kpi.value}
                                    </div>

                                    {/* Label */}
                                    <div 
                                        className="text-sm uppercase text-gray-600 font-medium mb-3"
                                        style={{
                                            fontFamily: 'Nunito, sans-serif'
                                        }}
                                    >
                                        {kpi.label}
                                    </div>

                                    {/* Trend and Sparkline */}
                                    <div className="flex items-center gap-2">
                                        {/* Trend Arrow and Value */}
                                        {kpi.trend !== 'none' && (
                                            <div className="flex items-center gap-1">
                                                {renderTrendArrow(kpi.trend)}
                                                <span 
                                                    className={`text-xs font-medium ${getTrendColor(kpi.trend)}`}
                                                    style={{
                                                        fontFamily: 'Nunito, sans-serif'
                                                    }}
                                                >
                                                    {kpi.trendValue}
                                                </span>
                                            </div>
                                        )}

                                        {/* Mini Sparkline */}
                                        {kpi.sparklineData && renderSparkline(kpi.sparklineData)}
                                    </div>
                                </div>
                            ))}
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
                            {slideData?.description || 'These key metrics provide a comprehensive overview of business performance, tracking essential indicators that drive strategic decision-making and operational excellence.'}
                        </p>
                    </div>
                </div>
            </div>
        </>
    )
}

export default KPISummaryGridSlideLayout 