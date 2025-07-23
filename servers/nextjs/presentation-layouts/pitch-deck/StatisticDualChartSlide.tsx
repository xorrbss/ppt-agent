import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { BarChart, Bar, AreaChart, Area, XAxis, YAxis, CartesianGrid, ResponsiveContainer } from "recharts";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("STATISTIC")
        .meta({
            description: "Main title for the statistic section",
        }),

    companyLogo: ImageSchema.default({
        __image_url__: "https://via.placeholder.com/40x40/FFFFFF/1D9A8A?text=C",
        __image_prompt__: "Clean modern company logo icon in white"
    }).meta({
        description: "Company logo icon",
    }),

    companyName: z.string()
        .min(2)
        .max(25)
        .default("Company Name")
        .meta({
            description: "Company name for branding",
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
        description: "Bar chart data for customer satisfaction",
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
        description: "Area chart data for repeat order rate",
    }),

    leftChartTitle: z.string()
        .min(5)
        .max(40)
        .default("Our Customer's Satisfaction")
        .meta({
            description: "Title for the left chart",
        }),

    leftChartDescription: z.string()
        .min(20)
        .max(200)
        .default("An impressive client satisfaction rate underscores our unwavering commitment to delivering exceptional service and exceeding expectations.")
        .meta({
            description: "Description for the left chart",
        }),

    rightChartTitle: z.string()
        .min(5)
        .max(40)
        .default("Repeat Order Rate")
        .meta({
            description: "Title for the right chart",
        }),

    rightChartDescription: z.string()
        .min(20)
        .max(200)
        .default("Our remarkable client repeat order rate of 123 times are testament to the quality of our products/services and the trust our clients place in our ability.")
        .meta({
            description: "Description for the right chart",
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

    const {
        mainTitle,
        companyLogo,
        companyName,
        barChartData,
        areaChartData,
        leftChartTitle,
        leftChartDescription,
        rightChartTitle,
        rightChartDescription
    } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Header Section */}
            <div className="h-20 bg-teal-600 px-16 py-4 flex justify-between items-center">
                {/* Title */}
                {mainTitle && (
                    <h1 className="text-4xl font-black text-white">
                        {mainTitle}
                    </h1>
                )}

                {/* Company Branding */}
                <div className="flex items-center space-x-3">
                    {companyLogo?.__image_url__ && (
                        <div className="w-8 h-8">
                            <img
                                src={companyLogo.__image_url__}
                                alt={companyLogo.__image_prompt__}
                                className="w-full h-full object-contain"
                            />
                        </div>
                    )}
                    {companyName && (
                        <span className="text-lg font-bold text-white">
                            {companyName}
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
                                <ResponsiveContainer width="100%" height="100%">
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
                                </ResponsiveContainer>
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
                                <ResponsiveContainer width="100%" height="100%">
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
                                </ResponsiveContainer>
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