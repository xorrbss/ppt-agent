import React from "react";
import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { BarChart, Bar, AreaChart, Area, XAxis, YAxis, CartesianGrid } from "recharts";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("PERFORMANCE METRICS")
        .meta({
            description: "Main section heading - adapt to presentation topic (e.g., 'Climate Analysis', 'Health Outcomes', 'Research Data', 'Impact Assessment')",
        }),

    organizationName: z.string()
        .min(2)
        .max(30)
        .default("Your Organization")
        .meta({
            description: "Name of the organization or entity presenting the data",
        }),

    brandLogo: ImageSchema.default({
        __image_url__: "https://via.placeholder.com/40x40/22C55E/FFFFFF?text=L",
        __image_prompt__: "Professional organization logo - clean and modern design"
    }).meta({
        description: "Logo or brand mark representing the organization",
    }),

    barChartData: z.array(z.object({
        name: z.string(),
        series1: z.number(),
        series2: z.number(),
        series3: z.number()
    })).min(5).max(5).default([
        { name: "Item 1", series1: 5, series2: 5, series3: 8 },
        { name: "Item 2", series1: 8, series2: 8, series3: 15 },
        { name: "Item 3", series1: 15, series2: 10, series3: 18 },
        { name: "Item 4", series1: 18, series2: 14, series3: 22 },
        { name: "Item 5", series1: 22, series2: 20, series3: 8 }
    ]).meta({
        description: "CRITICAL: Provide topic-specific data for the left bar chart. For global warming: 5 years of data (2020-2024) with CO2 emissions by sector (Transport, Industry, Energy) with actual values. For healthcare: Patient outcomes across 5 categories (Prevention, Treatment, Recovery) with real percentages. For education: Student performance across 5 metrics (Reading, Math, Science) with grade levels. Use realistic data patterns and values.",
    }),

    areaChartData: z.array(z.object({
        name: z.string(),
        series1: z.number(),
        series2: z.number(),
        series3: z.number()
    })).min(5).max(5).default([
        { name: "Item 1", series1: 20, series2: 30, series3: 15 },
        { name: "Item 2", series1: 40, series2: 45, series3: 35 },
        { name: "Item 3", series1: 45, series2: 50, series3: 80 },
        { name: "Item 4", series1: 50, series2: 45, series3: 85 },
        { name: "Item 5", series1: 80, series2: 75, series3: 120 }
    ]).meta({
        description: "CRITICAL: Provide topic-specific data for the right area chart. For global warming: Cumulative data over 5 time periods showing renewable energy adoption, carbon reduction efforts, and policy implementations with realistic growth trends. For healthcare: Cumulative patient care metrics showing improvement over time. For education: Progressive learning outcomes showing student advancement. Ensure data shows meaningful trends relevant to the topic.",
    }),

    leftChartTitle: z.string()
        .min(5)
        .max(40)
        .default("Our Customer's Satisfaction")
        .meta({
            description: "IMPORTANT: Provide topic-specific title for left chart. For global warming: 'Global CO2 Emissions by Sector', 'Temperature Rise by Region', 'Renewable Energy Adoption'. For healthcare: 'Patient Treatment Outcomes', 'Healthcare Quality Metrics', 'Recovery Success Rates'. For education: 'Student Performance by Subject', 'Learning Progress Assessment', 'Academic Achievement Trends'.",
        }),

    leftChartDescription: z.string()
        .min(20)
        .max(200)
        .default("An impressive client satisfaction rate underscores our unwavering commitment to delivering exceptional service and exceeding expectations.")
        .meta({
            description: "ESSENTIAL: Provide topic-relevant description explaining the left chart data. For global warming: Explain emission sources, trends, and implications. For healthcare: Describe treatment effectiveness and patient outcomes. For education: Explain performance metrics and learning indicators. Make it informative and specific to the data shown.",
        }),

    rightChartTitle: z.string()
        .min(5)
        .max(40)
        .default("Repeat Order Rate")
        .meta({
            description: "IMPORTANT: Provide topic-specific title for right chart. For global warming: 'Climate Action Progress', 'Carbon Reduction Timeline', 'Sustainability Milestones'. For healthcare: 'Patient Recovery Timeline', 'Treatment Progress Tracking', 'Health Improvement Trajectory'. For education: 'Learning Progress Over Time', 'Student Development Path', 'Academic Growth Timeline'.",
        }),

    rightChartDescription: z.string()
        .min(20)
        .max(200)
        .default("Our remarkable client repeat order rate of 123 times are testament to the quality of our products/services and the trust our clients place in our ability.")
        .meta({
            description: "ESSENTIAL: Provide topic-relevant description explaining the right chart's cumulative/timeline data. For global warming: Describe progress in climate action, policy impact, or environmental improvements. For healthcare: Explain patient journey and recovery progression. For education: Describe learning advancement and skill development over time. Make it specific and data-driven.",
        }),
})

// Chart configuration
const chartConfig = {
    series1: {
        label: "Series 1",
        color: "#1D9A8A",
    },
    series2: {
        label: "Series 2",
        color: "#A8C97F",
    },
    series3: {
        label: "Series 3",
        color: "#E8F4B8",
    },
};

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const StatisticDualChartSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, organizationName, brandLogo, barChartData, areaChartData, leftChartTitle, leftChartDescription, rightChartTitle, rightChartDescription } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Header Section */}
            <div className="h-20 bg-teal-600 px-16 py-4 flex justify-between items-center">
                {/* Title */}
                {sectionTitle && (
                    <h1 className="text-4xl font-black text-white">
                        {sectionTitle}
                    </h1>
                )}

                {/* Company Branding */}
                <div className="flex items-center space-x-3">
                    {brandLogo?.__image_url__ && (
                        <div className="w-8 h-8">
                            <img
                                src={brandLogo.__image_url__}
                                alt={brandLogo.__image_prompt__}
                                className="w-full h-full object-contain"
                            />
                        </div>
                    )}
                    {organizationName && (
                        <span className="text-lg font-bold text-white">
                            {organizationName}
                        </span>
                    )}
                </div>
            </div>

            {/* Content Section */}
            <div className="flex-1 h-[calc(100%-80px)] flex">
                {/* Left Chart Section */}
                <div className="w-1/2 p-8 bg-gray-50 flex flex-col">
                    {/* Chart Legend */}
                    <div className="flex items-center justify-start mb-4 space-x-4">
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-teal-600 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 1</span>
                        </div>
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-green-400 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 2</span>
                        </div>
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-yellow-200 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 3</span>
                        </div>
                    </div>

                    {/* Bar Chart */}
                    {barChartData && barChartData.length > 0 && (
                        <div className="flex-1 mb-6">
                            <ChartContainer config={chartConfig} className="h-full w-full">

                                <BarChart
                                    data={barChartData}
                                    margin={{ top: 10, right: 20, left: 0, bottom: 30 }}
                                    barCategoryGap="20%"
                                >
                                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                                    <XAxis
                                        dataKey="name"
                                        axisLine={false}
                                        tickLine={false}
                                        tick={{ fontSize: 12, fill: '#666' }}
                                    />
                                    <YAxis
                                        axisLine={false}
                                        tickLine={false}
                                        tick={{ fontSize: 12, fill: '#666' }}
                                    />
                                    <ChartTooltip content={<ChartTooltipContent />} />
                                    <Bar
                                        dataKey="series1"
                                        fill="#1D9A8A"
                                        radius={[2, 2, 0, 0]}
                                        barSize={15}
                                    />
                                    <Bar
                                        dataKey="series2"
                                        fill="#A8C97F"
                                        radius={[2, 2, 0, 0]}
                                        barSize={15}
                                    />
                                    <Bar
                                        dataKey="series3"
                                        fill="#E8F4B8"
                                        radius={[2, 2, 0, 0]}
                                        barSize={15}
                                    />
                                </BarChart>

                            </ChartContainer>
                        </div>
                    )}

                    {/* Chart Description */}
                    <div className="space-y-3">
                        {leftChartTitle && (
                            <h3 className="text-xl font-bold text-gray-900">
                                {leftChartTitle}
                            </h3>
                        )}
                        {leftChartDescription && (
                            <p className="text-base leading-relaxed text-gray-700">
                                {leftChartDescription}
                            </p>
                        )}
                    </div>
                </div>

                {/* Right Chart Section */}
                <div className="w-1/2 p-8 bg-white flex flex-col">
                    {/* Chart Legend */}
                    <div className="flex items-center justify-end mb-4 space-x-4">
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-yellow-200 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 1</span>
                        </div>
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-green-400 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 2</span>
                        </div>
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-teal-600 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 3</span>
                        </div>
                    </div>

                    {/* Area Chart */}
                    {areaChartData && areaChartData.length > 0 && (
                        <div className="flex-1 mb-6">
                            <ChartContainer config={chartConfig} className="h-full w-full">

                                <AreaChart
                                    data={areaChartData}
                                    margin={{ top: 10, right: 20, left: 0, bottom: 30 }}
                                >
                                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                                    <XAxis
                                        dataKey="name"
                                        axisLine={false}
                                        tickLine={false}
                                        tick={{ fontSize: 12, fill: '#666' }}
                                    />
                                    <YAxis
                                        axisLine={false}
                                        tickLine={false}
                                        tick={{ fontSize: 12, fill: '#666' }}
                                    />
                                    <ChartTooltip content={<ChartTooltipContent />} />
                                    <Area
                                        type="monotone"
                                        dataKey="series3"
                                        stackId="1"
                                        stroke="#1D9A8A"
                                        fill="#1D9A8A"
                                        fillOpacity={0.8}
                                    />
                                    <Area
                                        type="monotone"
                                        dataKey="series2"
                                        stackId="1"
                                        stroke="#A8C97F"
                                        fill="#A8C97F"
                                        fillOpacity={0.8}
                                    />
                                    <Area
                                        type="monotone"
                                        dataKey="series1"
                                        stackId="1"
                                        stroke="#E8F4B8"
                                        fill="#E8F4B8"
                                        fillOpacity={0.8}
                                    />
                                </AreaChart>

                            </ChartContainer>
                        </div>
                    )}

                    {/* Chart Description */}
                    <div className="space-y-3">
                        {rightChartTitle && (
                            <h3 className="text-xl font-bold text-gray-900">
                                {rightChartTitle}
                            </h3>
                        )}
                        {rightChartDescription && (
                            <p className="text-base leading-relaxed text-gray-700">
                                {rightChartDescription}
                            </p>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default StatisticDualChartSlide;