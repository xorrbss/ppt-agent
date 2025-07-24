import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import React from 'react'
import { BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Cell } from "recharts";
import * as z from "zod";

export const layoutId = 'type4-slide'
export const layoutName = 'Type4 Slide'
export const layoutDescription = 'A chart-focused layout with title, chart visualization, and description text.'

const chartDataSchema = z.object({
    name: z.string().meta({ description: "Data point name" }),
    value: z.number().meta({ description: "Data point value" }),
    category: z.string().optional().meta({ description: "Category for grouping" }),
    x: z.number().optional().meta({ description: "X coordinate for scatter plots" }),
    y: z.number().optional().meta({ description: "Y coordinate for scatter plots" }),
});


const type4SlideSchema = z.object({
    title: z.string().min(3).max(50).default('Chart Analysis').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(130).default('This chart shows important data trends and insights that help understand the current situation and make informed decisions.').meta({
        description: "Description text for the chart",
    }),
    chartData: z.any().optional().meta({
        description: "Chart data object",
    }),
    isFullSizeChart: z.boolean().default(false).meta({
        description: "Whether to display chart in full size mode",
    }),
    chartType: z.enum(['bar', 'line', 'pie', 'area', 'scatter']).default('bar').meta({
        description: "Type of chart to display",
    }),
    data: z.array(chartDataSchema).min(2).max(10).default([
        { name: '2021', value: 5 },
        { name: '2022', value: 12 },
        { name: '2023', value: 18 },
        { name: '2024', value: 23 },
        { name: '2025', value: 26 },
    ]).meta({
        description: "Chart data points",
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

export const Schema = type4SlideSchema

export type Type4SlideData = z.infer<typeof type4SlideSchema>

interface Type4SlideLayoutProps {
    data: Partial<Type4SlideData>
}

const Type4SlideLayout: React.FC<Type4SlideLayoutProps> = ({ data: slideData }) => {

    const { title, description, data, dataKey, categoryKey, color, showLegend = false, showTooltip = true, chartType = 'bar' } = slideData;

    const chartData = data || [];
    const renderChart = () => {
        const commonProps = {
            data: chartData,
            margin: { top: 10, right: 20, left: 0, bottom: 30 },
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
                        <Bar dataKey={dataKey || 'value'} fill={color} radius={[4, 4, 0, 0]} />
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
                            dataKey={dataKey || 'value'}
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
                            dataKey={dataKey || 'value'}
                            stroke={color}
                            fill={color}
                            fillOpacity={0.6}
                        />
                    </AreaChart>
                );

            case 'pie':
                return (
                    <PieChart margin={{ top: 10, right: 10, left: 10, bottom: 10 }}>
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Pie
                            data={chartData}
                            cx="50%"
                            cy="40%"
                            outerRadius={70}
                            fill={color}
                            dataKey={dataKey || 'value'}
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
                        <Scatter dataKey={dataKey || 'value'} fill={color} />
                    </ScatterChart>
                );

            default:
                return <div>Unsupported chart type</div>;
        }
    };

    return (
        <div
            className=" rounded-sm w-full max-w-[1280px] px-3 py-[10px] sm:px-12 lg:px-20 sm:py-[40px] lg:py-[86px] shadow-lg max-h-[720px] flex flex-col items-center justify-center aspect-video bg-white relative z-20 mx-auto"

        >
            {title && <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight mb-4 lg:mb-8">
                {title}
            </h1>}

            <div className={`flex w-full  items-center  `}>
                <div className="w-full">
                    <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-100 p-4">
                        <ChartContainer config={chartConfig} className="h-full w-full">
                            {renderChart()}
                        </ChartContainer>
                    </div>
                </div>
                <div className="w-full text-center">
                    {description && <p className={`text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal`}>
                        {description}
                    </p>}
                </div>
            </div>
        </div>
    )
}

export default Type4SlideLayout

