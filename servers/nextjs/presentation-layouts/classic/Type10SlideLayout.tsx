import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import React from 'react'
import { BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Cell, ResponsiveContainer } from "recharts";
import * as z from "zod";
import { IconSchema } from '../defaultSchemes';

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


const type10SlideSchema = z.object({
    title: z.string().min(3).max(50).default('Chart Analysis').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(8).max(40).default('This is a description of the chart analysis').meta({
        description: " Short description of the chart analysis",
    }),
    items: z.array(z.object({
        icon: IconSchema.meta({
            description: "Item icon",
        }),
        heading: z.string().min(2).max(50).meta({
            description: "Item heading",
        }),
        description: z.string().min(10).max(130).meta({
            description: "Item description",
        })
    })).min(2).max(3).default(() => [
        {
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                __icon_query__: 'A beautiful road in the mountains'
            },
            heading: 'First Key Point',
            description: 'Detailed explanation of the first important point that supports the main topic'
        },
        {
            icon: {
                __icon_url__: 'https://cdn.pixabay.com/photo/2015/12/01/20/28/road-1072823_1280.jpg',
                __icon_query__: 'A beautiful road in the mountains'
            },
            heading: 'Second Key Point',
            description: 'Detailed explanation of the second important point with relevant information'
        }
    ]).meta({
        description: "List of numbered items (2-3 items)",
    }),
    chartData: z.any().optional().meta({
        description: "Chart data object",
    }),
    isFullSizeChart: z.boolean().default(false).meta({
        description: "Whether to display chart in full size mode",
    }),
    chartType: z.enum(['bar', 'line', 'pie', 'area', 'scatter']).default('line').meta({
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

export const Schema = type10SlideSchema

export type Type10SlideData = z.infer<typeof type10SlideSchema>

interface Type10SlideLayoutProps {
    data: Partial<Type10SlideData>
}

const Type10SlideLayout: React.FC<Type10SlideLayoutProps> = ({ data: slideData }) => {
    const { title, items, data, chartType = 'line', color = '#3b82f6', dataKey = 'value', categoryKey = 'name', showLegend = false, showTooltip = true } = slideData;
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
                    <PieChart margin={{ top: 10, right: 10, left: 10, bottom: 10 }}>
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Pie
                            data={chartData}
                            cx="50%"
                            cy="40%"
                            outerRadius={70}
                            fill={color}
                            dataKey={dataKey}
                            label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                        >
                            {chartData.map((entry: any, index: number) => (
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
        <div
            className=" rounded-sm w-full max-w-[1280px] px-3 py-[10px] sm:px-12 lg:px-20 sm:py-[40px] lg:py-[86px] shadow-lg max-h-[720px] flex flex-col items-center justify-center aspect-video bg-white relative z-20 mx-auto"

        >
            <div className='w-full flex flex-col items-start justify-start'>

                {title && <h1 className="text-2xl text-start sm:text-3xl lg:text-4xl xl:text-5xl font-bold text-gray-900 leading-tight mb-4 lg:mb-8">
                    {title || 'Chart Analysis'}
                </h1>}

            </div>
            <div className={`flex gap-6 w-full  items-center  `}>
                <div className="w-1/2">
                    <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-100 p-4">
                        <ChartContainer config={chartConfig} className="h-full w-full">
                            {renderChart()}
                        </ChartContainer>
                    </div>
                </div>
                <div className="lg:w-1/2 relative">
                    <div className="space-y-3 lg:space-y-6">
                        {items && items.map((item, index) => (
                            <div
                                key={index}
                                style={{
                                    boxShadow: "0 2px 10px 0 rgba(43, 43, 43, 0.2)",
                                }}
                                className="rounded-lg p-3 lg:p-6 relative"
                            >
                                <div className="flex gap-6">
                                    <div className="w-[48px] h-[48px]">
                                        <div className="w-full h-full bg-blue-600 rounded-lg flex items-center justify-center overflow-hidden">
                                            {item.icon?.__icon_url__ && <img
                                                src={item.icon?.__icon_url__ || ''}
                                                alt={item.icon?.__icon_query__ || item.heading}
                                                className="w-full h-full object-cover"
                                            />}
                                        </div>
                                    </div>
                                    <div className="w-[calc(100%-55px)] space-y-1">
                                        {item.heading && <h3 className="text-gray-900 text-base sm:text-lg lg:text-[24px] leading-[26px] lg:leading-[32px] font-bold">
                                            {item.heading}
                                        </h3>}
                                        {item.description && <p className="text-gray-700 text-sm sm:text-base lg:text-[20px] leading-[20px] lg:leading-[30px] font-normal">
                                            {item.description}
                                        </p>}
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    )
}

export default Type10SlideLayout

