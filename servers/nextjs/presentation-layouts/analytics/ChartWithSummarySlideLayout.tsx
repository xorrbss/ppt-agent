import React from 'react'
import * as z from "zod";
import { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent } from "@/components/ui/chart";
import { BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Cell, ResponsiveContainer } from "recharts";

export const layoutId = 'chart-with-summary-slide'
export const layoutName = 'Chart with Summary Slide'
export const layoutDescription = 'A layout for displaying data visualization with interpretation and summary points.'

const chartDataSchema = z.object({
    name: z.string().meta({ description: "Data point name" }),
    value: z.number().meta({ description: "Data point value" }),
    category: z.string().optional().meta({ description: "Category for grouping" }),
    x: z.number().optional().meta({ description: "X coordinate for scatter plots" }),
    y: z.number().optional().meta({ description: "Y coordinate for scatter plots" }),
});

const summaryPointSchema = z.object({
    point: z.string().min(5).max(200).meta({ description: "Summary point or insight" }),
});

const chartWithSummarySlideSchema = z.object({
    title: z.string().min(3).max(100).default('Sales Performance Analysis').meta({
        description: "Main title of the slide",
    }),
    chartType: z.enum(['bar', 'line', 'pie', 'area', 'scatter']).default('bar').meta({
        description: "Type of chart to display",
    }),
    data: z.array(chartDataSchema).min(2).max(10).default([
        { name: 'Q1', value: 4000 },
        { name: 'Q2', value: 3000 },
        { name: 'Q3', value: 5000 },
        { name: 'Q4', value: 4500 },
    ]).meta({
        description: "Chart data points",
    }),
    summaryPoints: z.array(summaryPointSchema).min(2).max(5).default([
        { point: 'Q3 showed the highest performance with 25% growth' },
        { point: 'Q2 experienced a temporary dip due to market conditions' },
        { point: 'Overall annual growth trend remains positive' },
        { point: 'Target exceeded by 12% for the fiscal year' },
    ]).meta({
        description: "Key insights and summary points",
    }),
    dataKey: z.string().default('value').meta({
        description: "Key field for chart values",
    }),
    categoryKey: z.string().default('name').meta({
        description: "Key field for chart categories",
    }),
    color: z.string().default('#3b82f6').meta({
        description: "Primary color for chart elements",
    }),
    showLegend: z.boolean().default(false).meta({
        description: "Whether to show chart legend",
    }),
    showTooltip: z.boolean().default(true).meta({
        description: "Whether to show chart tooltip",
    }),
})

export const Schema = chartWithSummarySlideSchema

export type ChartWithSummarySlideData = z.infer<typeof chartWithSummarySlideSchema>

interface ChartWithSummarySlideLayoutProps {
    data?: Partial<ChartWithSummarySlideData>
}

const chartConfig = {
    value: {
        label: "Value",
    },
    name: {
        label: "Name",
    },
};

const CHART_COLORS = [
    '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', 
    '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#6366f1'
];

const ChartWithSummarySlideLayout: React.FC<ChartWithSummarySlideLayoutProps> = ({ data: slideData }) => {
    const chartData = slideData?.data || [];
    const chartType = slideData?.chartType || 'bar';
    const color = slideData?.color || '#3b82f6';
    const dataKey = slideData?.dataKey || 'value';
    const categoryKey = slideData?.categoryKey || 'name';
    const showLegend = slideData?.showLegend || false;
    const showTooltip = slideData?.showTooltip || true;
    const summaryPoints = slideData?.summaryPoints || [];

    const renderChart = () => {
        const commonProps = {
            data: chartData,
            margin: { top: 20, right: 30, left: 40, bottom: 80 },
        };

        switch (chartType) {
            case 'bar':
                return (
                    <BarChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey={categoryKey} />
                        <YAxis />
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Bar dataKey={dataKey} fill={color} radius={[4, 4, 0, 0]} />
                    </BarChart>
                );
            
            case 'line':
                return (
                    <LineChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey={categoryKey} />
                        <YAxis />
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Line 
                            type="monotone" 
                            dataKey={dataKey} 
                            stroke={color} 
                            strokeWidth={3}
                            dot={{ fill: color, strokeWidth: 2, r: 4 }}
                        />
                    </LineChart>
                );
            
            case 'area':
                return (
                    <AreaChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey={categoryKey} />
                        <YAxis />
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Area 
                            type="monotone" 
                            dataKey={dataKey} 
                            stroke={color} 
                            fill={color} 
                            fillOpacity={0.6}
                        />
                    </AreaChart>
                );
            
            case 'pie':
                return (
                    <PieChart margin={{ top: 20, right: 30, left: 40, bottom: 80 }}>
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Pie
                            data={chartData}
                            cx="50%"
                            cy="40%"
                            outerRadius={80}
                            fill={color}
                            dataKey={dataKey}
                            label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                        >
                            {chartData.map((entry, index) => (
                                <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                            ))}
                        </Pie>
                    </PieChart>
                );
            
            case 'scatter':
                return (
                    <ScatterChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="x" type="number" />
                        <YAxis dataKey="y" type="number" />
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Scatter dataKey="value" fill={color} />
                    </ScatterChart>
                );
            
            default:
                return <div>Unsupported chart type</div>;
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
                    <div className="flex-shrink-0 h-16 sm:h-20 flex items-center">
                        <h1 
                            className="text-3xl sm:text-3xl lg:text-4xl font-bold text-gray-900 leading-tight text-left"
                            style={{
                                fontFamily: 'Space Grotesk, sans-serif'
                            }}
                        >
                            {slideData?.title || 'Sales Performance Analysis'}
                        </h1>
                    </div>

                    {/* Chart and Summary section */}
                    <div className="flex-1 w-full overflow-hidden">
                        <div className="flex gap-6 h-full items-center">
                            {/* Chart section - smaller flex */}
                            <div className="flex-[5] h-full">
                                <div className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-4 sm:p-6 h-full overflow-hidden">
                                    <ChartContainer config={chartConfig} className="h-full w-full max-h-[500px] sm:max-h-[520px] lg:max-h-[540px]">
                                        {renderChart()}
                                    </ChartContainer>
                                </div>
                            </div>

                            {/* Summary section - larger flex */}
                            <div className="flex-[4] h-full flex items-center">
                                <div className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-4 sm:p-6 h-full overflow-hidden">
                                    <div className="flex flex-col justify-center h-full">
                                        <h2 
                                            className="text-xl sm:text-2xl font-bold text-gray-900 mb-4"
                                            style={{
                                                fontFamily: 'Space Grotesk, sans-serif'
                                            }}
                                        >
                                            Key Insights
                                        </h2>
                                        <ul className="space-y-3">
                                            {summaryPoints.map((item, index) => (
                                                <li 
                                                    key={index}
                                                    className="flex items-start gap-3"
                                                >
                                                    <div className="w-2 h-2 bg-blue-500 rounded-full mt-2 flex-shrink-0"></div>
                                                    <span 
                                                        className="text-sm sm:text-base text-gray-700 leading-relaxed"
                                                        style={{
                                                            fontFamily: 'Nunito, sans-serif'
                                                        }}
                                                    >
                                                        {item.point}
                                                    </span>
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </>
    )
}

export default ChartWithSummarySlideLayout 