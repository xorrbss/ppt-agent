import React from 'react'
import * as z from "zod";
import { ChartContainer, ChartTooltip, ChartTooltipContent, ChartLegend, ChartLegendContent } from "@/components/ui/chart";
import { BarChart, Bar, LineChart, Line, PieChart, Pie, AreaChart, Area, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Cell, ResponsiveContainer } from "recharts";

export const layoutId = 'chart-table-slide'
export const layoutName = 'Chart + Table Slide'
export const layoutDescription = 'A layout for displaying data visualization alongside detailed tabular data for comprehensive analysis.'

const chartDataSchema = z.object({
    name: z.string().meta({ description: "Data point name" }),
    value: z.number().meta({ description: "Data point value" }),
    category: z.string().optional().meta({ description: "Category for grouping" }),
    x: z.number().optional().meta({ description: "X coordinate for scatter plots" }),
    y: z.number().optional().meta({ description: "Y coordinate for scatter plots" }),
});

const tableRowSchema = z.object({
    metric: z.string().meta({ description: "Metric name" }),
    value: z.string().meta({ description: "Metric value" }),
    change: z.string().optional().meta({ description: "Change percentage or indicator" }),
    status: z.string().optional().meta({ description: "Status or category" }),
});

const chartTableSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Revenue Analysis & Breakdown').meta({
        description: "Main title of the slide",
    }),
    description: z.string().min(10).max(300).default('This comprehensive analysis combines visual trends with detailed metrics, providing both high-level insights and granular data for informed decision-making.').meta({
        description: "Bottom description text",
    }),
    chartType: z.enum(['bar', 'line', 'pie', 'area', 'scatter']).default('bar').meta({
        description: "Type of chart to display",
    }),
    chartData: z.array(chartDataSchema).min(2).max(10).default([
        { name: 'Q1', value: 4000 },
        { name: 'Q2', value: 3000 },
        { name: 'Q3', value: 5000 },
        { name: 'Q4', value: 4500 },
    ]).meta({
        description: "Chart data points",
    }),
    tableData: z.array(tableRowSchema).min(3).max(15).default([
        { metric: 'Total Revenue', value: '$16.5M', change: '+12.5%', status: 'Growth' },
        { metric: 'Q1 Revenue', value: '$4.0M', change: '+8.2%', status: 'Stable' },
        { metric: 'Q2 Revenue', value: '$3.0M', change: '-15.3%', status: 'Decline' },
        { metric: 'Q3 Revenue', value: '$5.0M', change: '+25.8%', status: 'Growth' },
        { metric: 'Q4 Revenue', value: '$4.5M', change: '+18.4%', status: 'Growth' },
        { metric: 'Average Deal Size', value: '$125K', change: '+5.2%', status: 'Stable' },
        { metric: 'Customer Count', value: '132', change: '+22.1%', status: 'Growth' },
        { metric: 'Market Share', value: '18.3%', change: '+3.1%', status: 'Growth' },
        { metric: 'Conversion Rate', value: '24.7%', change: '+1.8%', status: 'Stable' },
        { metric: 'Customer Churn', value: '5.2%', change: '-2.1%', status: 'Improvement' },
    ]).meta({
        description: "Table data rows",
    }),
    chartColor: z.string().default('#3b82f6').meta({
        description: "Primary color for chart elements",
    }),
    dataKey: z.string().default('value').meta({
        description: "Key field for chart values",
    }),
    categoryKey: z.string().default('name').meta({
        description: "Key field for chart categories",
    }),
    showLegend: z.boolean().default(false).meta({
        description: "Whether to show chart legend",
    }),
    showTooltip: z.boolean().default(true).meta({
        description: "Whether to show chart tooltip",
    }),
})

export const Schema = chartTableSlideSchema

export type ChartTableSlideData = z.infer<typeof chartTableSlideSchema>

interface ChartTableSlideLayoutProps {
    data?: Partial<ChartTableSlideData>
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

const ChartTableSlideLayout: React.FC<ChartTableSlideLayoutProps> = ({ data: slideData }) => {
    const chartData = slideData?.chartData || [];
    const tableData = slideData?.tableData || [];
    const chartType = slideData?.chartType || 'bar';
    const chartColor = slideData?.chartColor || '#3b82f6';
    const dataKey = slideData?.dataKey || 'value';
    const categoryKey = slideData?.categoryKey || 'name';
    const showLegend = slideData?.showLegend || false;
    const showTooltip = slideData?.showTooltip || true;

    const renderChart = () => {
        const commonProps = {
            data: chartData,
            margin: { top: 20, right: 20, left: 20, bottom: 40 },
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
                        <Bar dataKey={dataKey} fill={chartColor} radius={[4, 4, 0, 0]} />
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
                            stroke={chartColor} 
                            strokeWidth={3}
                            dot={{ fill: chartColor, strokeWidth: 2, r: 4 }}
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
                            stroke={chartColor} 
                            fill={chartColor} 
                            fillOpacity={0.6}
                        />
                    </AreaChart>
                );
            
            case 'pie':
                return (
                    <PieChart margin={{ top: 20, right: 20, left: 20, bottom: 40 }}>
                        {showTooltip && <ChartTooltip content={<ChartTooltipContent />} />}
                        {showLegend && <ChartLegend content={<ChartLegendContent />} />}
                        <Pie
                            data={chartData}
                            cx="50%"
                            cy="45%"
                            outerRadius={70}
                            fill={chartColor}
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
                        <Scatter dataKey="value" fill={chartColor} />
                    </ScatterChart>
                );
            
            default:
                return <div>Unsupported chart type</div>;
        }
    };

    const getStatusColor = (status: string) => {
        switch (status?.toLowerCase()) {
            case 'growth':
                return 'text-green-600';
            case 'decline':
                return 'text-red-600';
            case 'stable':
                return 'text-blue-600';
            case 'improvement':
                return 'text-emerald-600';
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
                            {slideData?.title || 'Revenue Analysis & Breakdown'}
                        </h1>
                    </div>

                    {/* Chart and Table section */}
                    <div className="flex-1 w-full overflow-hidden">
                        <div className="flex gap-6 h-full">
                            {/* Chart section - Left side */}
                            <div className="w-1/2 h-full">
                                <div className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-4 sm:p-6 h-full overflow-hidden">
                                    <ChartContainer config={chartConfig} className="h-full w-full max-h-[400px] sm:max-h-[420px] lg:max-h-[440px]">
                                        {renderChart()}
                                    </ChartContainer>
                                </div>
                            </div>

                            {/* Table section - Right side */}
                            <div className="w-1/2 h-full">
                                <div className="bg-white/30 backdrop-blur-sm rounded-xl border border-slate-200 shadow-md p-4 sm:p-6 h-full overflow-hidden">
                                    <div className="overflow-y-auto max-h-[360px] sm:max-h-[420px] lg:max-h-[440px]">
                                        <table className="w-full">
                                            <thead className="border-t border-b border-gray-300">
                                                <tr>
                                                    <th 
                                                        className="text-left py-3 px-2 text-sm font-semibold text-gray-900"
                                                        style={{ fontFamily: 'Space Grotesk, sans-serif' }}
                                                    >
                                                        Metric
                                                    </th>
                                                    <th 
                                                        className="text-right py-3 px-2 text-sm font-semibold text-gray-900"
                                                        style={{ fontFamily: 'Space Grotesk, sans-serif' }}
                                                    >
                                                        Value
                                                    </th>
                                                    <th 
                                                        className="text-right py-3 px-2 text-sm font-semibold text-gray-900"
                                                        style={{ fontFamily: 'Space Grotesk, sans-serif' }}
                                                    >
                                                        Change
                                                    </th>
                                                    <th 
                                                        className="text-center py-3 px-2 text-sm font-semibold text-gray-900"
                                                        style={{ fontFamily: 'Space Grotesk, sans-serif' }}
                                                    >
                                                        Status
                                                    </th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {tableData.map((row, index) => (
                                                    <tr key={index} className="border-b border-gray-200 hover:bg-white/20">
                                                        <td 
                                                            className="py-2 px-2 text-sm text-gray-700"
                                                            style={{ fontFamily: 'Nunito, sans-serif' }}
                                                        >
                                                            {row.metric}
                                                        </td>
                                                        <td 
                                                            className="py-2 px-2 text-sm text-gray-900 text-right font-medium"
                                                            style={{ fontFamily: 'Nunito, sans-serif' }}
                                                        >
                                                            {row.value}
                                                        </td>
                                                        <td 
                                                            className="py-2 px-2 text-sm text-right"
                                                            style={{ fontFamily: 'Nunito, sans-serif' }}
                                                        >
                                                            {row.change}
                                                        </td>
                                                        <td 
                                                            className={`py-2 px-2 text-sm text-center font-medium ${getStatusColor(row.status || '')}`}
                                                            style={{ fontFamily: 'Nunito, sans-serif' }}
                                                        >
                                                            {row.status}
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
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
                            {slideData?.description || 'This comprehensive analysis combines visual trends with detailed metrics, providing both high-level insights and granular data for informed decision-making.'}
                        </p>
                    </div>
                </div>
            </div>
        </>
    )
}

export default ChartTableSlideLayout 