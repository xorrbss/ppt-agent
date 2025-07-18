import React from 'react'
import * as z from "zod";
import { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent } from "@/components/ui/chart";
import { BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Cell, ResponsiveContainer } from "recharts";

export const layoutId = 'multi-chart-grid-slide'
export const layoutName = 'Multi-Chart Grid Slide'
export const layoutDescription = 'A layout for displaying 2-4 charts in a grid format for small multiples and trend comparisons.'

const chartDataSchema = z.object({
    name: z.string().meta({ description: "Data point name" }),
    value: z.number().meta({ description: "Data point value" }),
    category: z.string().optional().meta({ description: "Category for grouping" }),
    x: z.number().optional().meta({ description: "X coordinate for scatter plots" }),
    y: z.number().optional().meta({ description: "Y coordinate for scatter plots" }),
});

const chartItemSchema = z.object({
    type: z.enum(['bar', 'line', 'pie', 'area', 'scatter']).meta({ description: "Chart type" }),
    data: z.array(chartDataSchema).min(2).max(8).meta({ description: "Chart data points" }),
    title: z.string().min(2).max(50).meta({ description: "Chart title/caption" }),
    color: z.string().meta({ description: "Chart color" }),
});

const multiChartGridSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Market Performance Dashboard').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(300).default('This dashboard provides a comprehensive view of key performance indicators across multiple business segments, enabling quick identification of trends and comparative analysis.').meta({
        description: "Bottom description text",
    }),
    charts: z.array(chartItemSchema).min(2).max(4).default([
        {
            type: 'bar',
            data: [
                { name: 'Q1', value: 4000 },
                { name: 'Q2', value: 3000 },
                { name: 'Q3', value: 5000 },
                { name: 'Q4', value: 4500 },
            ],
            title: 'Revenue Trends',
            color: '#3b82f6'
        },
        {
            type: 'line',
            data: [
                { name: 'Q1', value: 2400 },
                { name: 'Q2', value: 2210 },
                { name: 'Q3', value: 2290 },
                { name: 'Q4', value: 2000 },
            ],
            title: 'Customer Growth',
            color: '#10b981'
        },
        {
            type: 'area',
            data: [
                { name: 'Q1', value: 1800 },
                { name: 'Q2', value: 1950 },
                { name: 'Q3', value: 2100 },
                { name: 'Q4', value: 2300 },
            ],
            title: 'Market Share',
            color: '#f59e0b'
        },
        {
            type: 'bar',
            data: [
                { name: 'Q1', value: 800 },
                { name: 'Q2', value: 967 },
                { name: 'Q3', value: 1200 },
                { name: 'Q4', value: 1400 },
            ],
            title: 'Cost Efficiency',
            color: '#ef4444'
        },
    ]).meta({
        description: "Array of charts (2-4 charts)",
    }),
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

export const Schema = multiChartGridSlideSchema

export type MultiChartGridSlideData = z.infer<typeof multiChartGridSlideSchema>

interface MultiChartGridSlideLayoutProps {
    data?: Partial<MultiChartGridSlideData>
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
    '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', 
    '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#6366f1'
];

const MultiChartGridSlideLayout: React.FC<MultiChartGridSlideLayoutProps> = ({ data: slideData }) => {
    const charts = slideData?.charts || [];
    const dataKey = slideData?.dataKey || 'value';
    const categoryKey = slideData?.categoryKey || 'name';
    const showLegend = slideData?.showLegend || false;
    const showTooltip = slideData?.showTooltip || true;

    // Determine grid layout based on chart count
    const getGridLayout = (count: number) => {
        switch (count) {
            case 2:
                return 'grid-cols-2 grid-rows-1';
            case 3:
                return 'grid-cols-2 grid-rows-2';
            case 4:
                return 'grid-cols-2 grid-rows-2';
            default:
                return 'grid-cols-2 grid-rows-2';
        }
    };

    const renderChart = (chartType: string, data: any[], color: string) => {
        const commonProps = {
            data: data,
            margin: { top: 5, right: 5, left: 5, bottom: 20 },
        };

        switch (chartType) {
            case 'bar':
                return (
                    <BarChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey={categoryKey} fontSize={10} />
                        <YAxis fontSize={10} />
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Bar dataKey={dataKey} fill={color} radius={[2, 2, 0, 0]} />
                    </BarChart>
                );
            
            case 'line':
                return (
                    <LineChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey={categoryKey} fontSize={10} />
                        <YAxis fontSize={10} />
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Line 
                            type="monotone" 
                            dataKey={dataKey} 
                            stroke={color} 
                            strokeWidth={2}
                            dot={{ fill: color, strokeWidth: 1, r: 2 }}
                        />
                    </LineChart>
                );
            
            case 'area':
                return (
                    <AreaChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey={categoryKey} fontSize={10} />
                        <YAxis fontSize={10} />
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
                    <PieChart margin={{ top: 5, right: 5, left: 5, bottom: 20 }}>
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Pie
                            data={data}
                            cx="50%"
                            cy="45%"
                            outerRadius={30}
                            fill={color}
                            dataKey={dataKey}
                            label={({ percent }) => `${(percent * 100).toFixed(0)}%`}
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
                        <XAxis dataKey="x" type="number" fontSize={10} />
                        <YAxis dataKey="y" type="number" fontSize={10} />
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Scatter dataKey="value" fill={color} />
                    </ScatterChart>
                );
            
            default:
                return <div className="text-xs text-center">Unsupported chart type</div>;
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
                            {slideData?.title || 'Market Performance Dashboard'}
                        </h1>
                    </div>

                    {/* Charts Grid section */}
                    <div className="flex-1 w-full overflow-hidden">
                        <div className={`grid ${getGridLayout(charts.length)} gap-4 h-full`}>
                            {charts.map((chart, index) => (
                                <div key={index} className="flex flex-col h-full">
                                    <div className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-2 sm:p-3 flex-1 overflow-hidden flex flex-col justify-end">
                                        <div className="h-40 w-full">
                                            <ChartContainer config={chartConfig} className="h-full w-full">
                                                {renderChart(chart.type, chart.data, chart.color)}
                                            </ChartContainer>
                                        </div>
                                    </div>
                                    <div className="mt-1">
                                        <p 
                                            className="text-xs text-center text-gray-700 font-medium"
                                            style={{
                                                fontFamily: 'Nunito, sans-serif'
                                            }}
                                        >
                                            {chart.title}
                                        </p>
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
                            {slideData?.description || 'This dashboard provides a comprehensive view of key performance indicators across multiple business segments, enabling quick identification of trends and comparative analysis.'}
                        </p>
                    </div>
                </div>
            </div>
        </>
    )
}

export default MultiChartGridSlideLayout 