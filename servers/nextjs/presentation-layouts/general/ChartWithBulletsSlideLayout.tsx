import React from 'react'
import * as z from "zod";
import { IconSchema } from '@/presentation-layouts/defaultSchemes';
import { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent } from "@/components/ui/chart";
import { BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Cell, ResponsiveContainer } from "recharts";

export const layoutId = 'chart-with-bullets-slide'
export const layoutName = 'Chart with Bullet Boxes'
export const layoutDescription = 'A slide layout with title, description, chart on the left and colored bullet boxes with icons on the right. Only choose this if data is available.'

const chartDataSchema = z.object({
    name: z.string().meta({ description: "Data point name" }),
    value: z.number().meta({ description: "Data point value" }),
    category: z.string().optional().meta({ description: "Category for grouping" }),
    x: z.number().optional().meta({ description: "X coordinate for scatter plots" }),
    y: z.number().optional().meta({ description: "Y coordinate for scatter plots" }),
});

const chartWithBulletsSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Market Size').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(500).default('Businesses face challenges with outdated technology and rising costs, limiting efficiency and growth in competitive markets.').meta({
        description: "Description text below the title",
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
    bulletPoints: z.array(z.object({
        title: z.string().min(2).max(100).meta({
            description: "Bullet point title",
        }),
        description: z.string().min(10).max(300).meta({
            description: "Bullet point description",
        }),
        icon: IconSchema,
    })).min(1).max(3).default([
        {
            title: 'Total Addressable Market',
            description: 'Companies can use TAM to plan future expansion and investment.',
            icon: {
                __icon_url__: 'https://cdn.jsdelivr.net/npm/lucide@latest/dist/esm/icons/target.js',
                __icon_query__: 'target market scope'
            }
        },
        {
            title: 'Serviceable Available Market',
            description: 'Indicates more measurable market segments for sales efforts.',
            icon: {
                __icon_url__: 'https://cdn.jsdelivr.net/npm/lucide@latest/dist/esm/icons/pie-chart.js',
                __icon_query__: 'pie chart analysis'
            }
        },
        {
            title: 'Serviceable Obtainable Market',
            description: 'Help companies plan development strategies according to the market.',
            icon: {
                __icon_url__: 'https://cdn.jsdelivr.net/npm/lucide@latest/dist/esm/icons/trending-up.js',
                __icon_query__: 'trending up growth'
            }
        }
    ]).meta({
        description: "List of bullet points with colored boxes and icons",
    })
})

export const Schema = chartWithBulletsSlideSchema

export type ChartWithBulletsSlideData = z.infer<typeof chartWithBulletsSlideSchema>

interface ChartWithBulletsSlideLayoutProps {
    data?: Partial<ChartWithBulletsSlideData>
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

const BULLET_COLORS = [
    '#7F31E9', '#2C78DA', '#F58AAB', '#10b981', '#f59e0b', 
    '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#6366f1'
];

const ChartWithBulletsSlideLayout: React.FC<ChartWithBulletsSlideLayoutProps> = ({ data: slideData }) => {
    const chartData = slideData?.data || [];
    const chartType = slideData?.chartType || 'bar';
    const color = slideData?.color || '#3b82f6';
    const dataKey = slideData?.dataKey || 'value';
    const categoryKey = slideData?.categoryKey || 'name';
    const showLegend = slideData?.showLegend || false;
    const showTooltip = slideData?.showTooltip || true;
    const bulletPoints = slideData?.bulletPoints || []

    const renderChart = () => {
        const commonProps = {
            data: chartData,
            margin: { top: 20, right: 30, left: 40, bottom: 60 },
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
                    <PieChart margin={{ top: 20, right: 30, left: 40, bottom: 60 }}>
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
                href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap" 
                rel="stylesheet"
            />
            
            <div 
                className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white relative z-20 mx-auto overflow-hidden"
                style={{
                    fontFamily: 'Poppins, sans-serif'
                }}
            >
                {/* Main Content */}
                <div className="flex h-full px-8 sm:px-12 lg:px-20 pt-8 pb-8">
                    {/* Left Section - Title, Description, Chart */}
                    <div className="flex-1 flex flex-col pr-8">
                        {/* Title */}
                        <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900 mb-4">
                            {slideData?.title || 'Market Size'}
                        </h1>

                        {/* Description */}
                        <p className="text-base text-gray-700 leading-relaxed mb-8">
                            {slideData?.description || 'Businesses face challenges with outdated technology and rising costs, limiting efficiency and growth in competitive markets.'}
                        </p>

                        {/* Chart Container */}
                        <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-100 p-4">
                            <ChartContainer config={chartConfig} className="h-full w-full">
                                {renderChart()}
                            </ChartContainer>
                        </div>
                    </div>

                    {/* Right Section - Bullet Point Boxes */}
                    <div className="flex-shrink-0 w-80 flex flex-col justify-center space-y-4">
                        {bulletPoints.map((bullet, index) => (
                            <div 
                                key={index} 
                                className="rounded-2xl p-6 text-white"
                                style={{
                                    backgroundColor: BULLET_COLORS[index % BULLET_COLORS.length]
                                }}
                            >
                                {/* Icon and Title */}
                                <div className="flex items-center space-x-3 mb-3">
                                    <div className="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
                                        <img 
                                            src={bullet.icon.__icon_url__} 
                                            alt={bullet.icon.__icon_query__}
                                            className="w-5 h-5 object-contain brightness-0 invert"
                                        />
                                    </div>
                                    <h3 className="text-lg font-semibold">
                                        {bullet.title}
                                    </h3>
                                </div>
                                
                                {/* Description */}
                                <p className="text-sm leading-relaxed opacity-90">
                                    {bullet.description}
                                </p>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </>
    )
}

export default ChartWithBulletsSlideLayout 