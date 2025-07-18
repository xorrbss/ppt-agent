import React from 'react'
import * as z from "zod";
import { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent } from "@/components/ui/chart";
import { BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Cell, ResponsiveContainer } from "recharts";

export const layoutId = 'two-charts-slide'
export const layoutName = 'Two Charts Slide'
export const layoutDescription = 'A layout for comparing two data visualizations side-by-side (e.g., Plan vs Actual).'

const chartDataSchema = z.object({
    name: z.string().meta({ description: "Data point name" }),
    value: z.number().meta({ description: "Data point value" }),
    category: z.string().optional().meta({ description: "Category for grouping" }),
    x: z.number().optional().meta({ description: "X coordinate for scatter plots" }),
    y: z.number().optional().meta({ description: "Y coordinate for scatter plots" }),
});

const twoChartsSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Plan vs Actual Comparison').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(300).default('This comparison shows the variance between planned targets and actual performance across quarterly metrics, highlighting areas of success and opportunities for improvement.').meta({
        description: "Bottom description text",
    }),
    // Chart A (Left)
    chartAType: z.enum(['bar', 'line', 'pie', 'area', 'scatter']).default('bar').meta({
        description: "Type of chart A to display",
    }),
    chartAData: z.array(chartDataSchema).min(2).max(10).default([
        { name: 'Q1', value: 4000 },
        { name: 'Q2', value: 3500 },
        { name: 'Q3', value: 5000 },
        { name: 'Q4', value: 4500 },
    ]).meta({
        description: "Chart A data points",
    }),
    chartATitle: z.string().min(2).max(50).default('Planned Revenue').meta({
        description: "Title/caption for chart A",
    }),
    chartAColor: z.string().default('#3b82f6').meta({
        description: "Primary color for chart A elements",
    }),
    // Chart B (Right)
    chartBType: z.enum(['bar', 'line', 'pie', 'area', 'scatter']).default('bar').meta({
        description: "Type of chart B to display",
    }),
    chartBData: z.array(chartDataSchema).min(2).max(10).default([
        { name: 'Q1', value: 3800 },
        { name: 'Q2', value: 3200 },
        { name: 'Q3', value: 5200 },
        { name: 'Q4', value: 4800 },
    ]).meta({
        description: "Chart B data points",
    }),
    chartBTitle: z.string().min(2).max(50).default('Actual Revenue').meta({
        description: "Title/caption for chart B",
    }),
    chartBColor: z.string().default('#10b981').meta({
        description: "Primary color for chart B elements",
    }),
    // Common settings
    dataKey: z.string().default('value').meta({
        description: "Key field for chart values",
    }),
    categoryKey: z.string().default('name').meta({
        description: "Key field for chart categories",
    }),
    showLegend: z.boolean().default(false).meta({
        description: "Whether to show chart legends",
    }),
    showTooltip: z.boolean().default(true).meta({
        description: "Whether to show chart tooltips",
    }),
})

export const Schema = twoChartsSlideSchema

export type TwoChartsSlideData = z.infer<typeof twoChartsSlideSchema>

interface TwoChartsSlideLayoutProps {
    data?: Partial<TwoChartsSlideData>
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

const TwoChartsSlideLayout: React.FC<TwoChartsSlideLayoutProps> = ({ data: slideData }) => {
    const chartAData = slideData?.chartAData || [];
    const chartBData = slideData?.chartBData || [];
    const chartAType = slideData?.chartAType || 'bar';
    const chartBType = slideData?.chartBType || 'bar';
    const chartAColor = slideData?.chartAColor || '#3b82f6';
    const chartBColor = slideData?.chartBColor || '#10b981';
    const dataKey = slideData?.dataKey || 'value';
    const categoryKey = slideData?.categoryKey || 'name';
    const showLegend = slideData?.showLegend || false;
    const showTooltip = slideData?.showTooltip || true;

    const renderChart = (chartType: string, data: any[], color: string) => {
        const commonProps = {
            data: data,
            margin: { top: 15, right: 15, left: 15, bottom: 35 },
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
                    <PieChart margin={{ top: 15, right: 15, left: 15, bottom: 35 }}>
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Pie
                            data={data}
                            cx="50%"
                            cy="40%"
                            outerRadius={50}
                            fill={color}
                            dataKey={dataKey}
                            label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                        >
                            {data.map((entry, index) => (
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
                className="w-full rounded-sm max-w-[1280px] shadow-md h-[720px] flex flex-col aspect-video bg-white relative z-20 mx-auto overflow-hidden"
                style={{
                    fontFamily: 'Nunito, sans-serif'
                }}
            >
                {/* Glass overlay background */}
                <div className="absolute inset-0 bg-white/20 backdrop-blur-sm rounded-sm border border-slate-200"></div>

                <div className="relative z-10 flex flex-col w-full h-full p-4 sm:p-6 lg:p-8">
                    {/* Header section */}
                    <div className="flex-shrink-0 h-16 sm:h-20 flex items-center justify-center">
                        <h1
                            className="text-3xl sm:text-3xl lg:text-4xl font-bold text-gray-900 leading-tight text-center"
                            style={{
                                fontFamily: 'Space Grotesk, sans-serif'
                            }}
                        >
                            {slideData?.title || 'Plan vs Actual Comparison'}
                        </h1>
                    </div>

                    {/* Two Charts section */}
                    <div className="flex-1 w-full overflow-hidden">
                        <div className="flex gap-6 h-full items-center">
                            {/* Chart A - Left side */}
                            <div className="w-1/2 h-full">
                                <div className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-4 sm:p-6 h-full overflow-hidden flex flex-col">
                                    <div className="flex-1">
                                        <ChartContainer config={chartConfig} className="h-full w-full max-h-[360px] sm:max-h-[380px] lg:max-h-[400px]">
                                            {renderChart(chartAType, chartAData, chartAColor)}
                                        </ChartContainer>
                                    </div>
                                    <div className="mt-2">
                                        <p
                                            className="text-sm text-center text-gray-700 font-medium"
                                            style={{
                                                fontFamily: 'Nunito, sans-serif'
                                            }}
                                        >
                                            {slideData?.chartATitle || 'Planned Revenue'}
                                        </p>
                                    </div>
                                </div>
                            </div>

                            {/* Chart B - Right side */}
                            <div className="w-1/2 h-full">
                                <div className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-4 sm:p-6 h-full overflow-hidden flex flex-col">
                                    <div className="flex-1">
                                        <ChartContainer config={chartConfig} className="h-full w-full max-h-[360px] sm:max-h-[380px] lg:max-h-[400px]">
                                            {renderChart(chartBType, chartBData, chartBColor)}
                                        </ChartContainer>
                                    </div>
                                    <div className="mt-2">
                                        <p
                                            className="text-sm text-center text-gray-700 font-medium"
                                            style={{
                                                fontFamily: 'Nunito, sans-serif'
                                            }}
                                        >
                                            {slideData?.chartBTitle || 'Actual Revenue'}
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Bottom Description section */}
                    <div className="flex-shrink-0 h-20 sm:h-24 flex items-center justify-center">
                        <p
                            className="text-sm sm:text-base text-center text-gray-700 leading-relaxed max-w-4xl px-4"
                            style={{
                                fontFamily: 'Nunito, sans-serif'
                            }}
                        >
                            {slideData?.description || 'This comparison shows the variance between planned targets and actual performance across quarterly metrics, highlighting areas of success and opportunities for improvement.'}
                        </p>
                    </div>
                </div>
            </div>
        </>
    )
}

export default TwoChartsSlideLayout 