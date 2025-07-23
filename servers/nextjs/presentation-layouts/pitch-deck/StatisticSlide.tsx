import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer } from "recharts";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(12)
        .default("STATISTIC")
        .meta({
            description: "Main title for the statistic section",
        }),

    subtitle: z.string()
        .min(10)
        .max(18)
        .default("CLIENT'S SATISFACTION")
        .meta({
            description: "Subtitle describing the statistic focus",
        }),

    description: z.string()
        .min(80)
        .max(200)
        .default("At the heart of our success lies the unwavering satisfaction of our clients. We take pride in fostering lasting partnerships, consistently exceeding expectations, and delivering results that not only meet but surpass the unique objectives of each client we serve.")
        .meta({
            description: "Description of client satisfaction approach",
        }),

    businessImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1552664730-d307ca884978?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Business team meeting discussing charts and documents at table"
    }).meta({
        description: "Business meeting image showing team collaboration",
    }),

    chartData: z.array(z.object({
        name: z.string(),
        series1: z.number(),
        series2: z.number(),
        series3: z.number()
    })).min(5).max(5).default([
        { name: "Item 1", series1: 18, series2: 0, series3: 0 },
        { name: "Item 2", series1: 30, series2: 12, series3: 8 },
        { name: "Item 3", series1: 26, series2: 38, series3: 20 },
        { name: "Item 4", series1: 40, series2: 30, series3: 35 },
        { name: "Item 5", series1: 42, series2: 45, series3: 32 }
    ]).meta({
        description: "Line chart data for satisfaction metrics",
    }),

    bulletPoints: z.array(z.object({
        text: z.string().min(20).max(100),
        color: z.enum(["teal", "beige", "light"])
    })).min(3).max(3).default([
        {
            text: "From brand positioning and messaging to visual identity, we guide you through every step.",
            color: "teal"
        },
        {
            text: "Navigate the path to increased sales with our insightful report and offering a strategic roadmap.",
            color: "beige"
        },
        {
            text: "Amplify your revenue streams, engage customers, and unlock the full potential of your business.",
            color: "light"
        }
    ]).meta({
        description: "Three bullet points with different colored indicators",
    }),

    showYellowUnderline: z.boolean()
        .default(true)
        .meta({
            description: "Show yellow decorative underline",
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
const StatisticSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, description, businessImage, chartData, bulletPoints, showYellowUnderline } = data;

    const getColorClass = (color: string) => {
        switch (color) {
            case "teal": return "bg-teal-600";
            case "beige": return "bg-yellow-300";
            case "light": return "bg-gray-300";
            default: return "bg-gray-300";
        }
    };

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Teal Background with Content and Image */}
                <div className="w-1/2 relative bg-teal-600">
                    {/* Top Content Section */}
                    <div className="px-16 pt-12 text-white flex-1">
                        {/* Title Section */}
                        <div className="mb-4">
                            {mainTitle && (
                                <h1 className="text-4xl lg:text-5xl font-black leading-tight mb-1">
                                    {mainTitle}
                                </h1>
                            )}

                            {subtitle && (
                                <p className="text-base font-semibold tracking-wide mb-1">
                                    {subtitle}
                                </p>
                            )}

                            {/* Yellow Decorative Underline */}
                            {showYellowUnderline && (
                                <div className="w-24 h-1 bg-yellow-300 mb-4"></div>
                            )}
                        </div>

                        {/* Description */}
                        {description && (
                            <div>
                                <p className="text-base leading-relaxed">
                                    {description}
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Bottom Business Image */}
                    <div className="relative h-80 mx-16 mt-10">
                        {businessImage?.__image_url__ && (

                            <img
                                src={businessImage.__image_url__}
                                alt={businessImage.__image_prompt__}
                                className="max-w-full w-full h-full object-cover"
                            />

                        )}


                    </div>
                </div>

                {/* Right Side - Chart and Bullet Points */}
                <div className="w-1/2 relative bg-white">
                    {/* Chart Section */}
                    <div className="flex-1 px-8 pt-8">
                        {/* Chart Legend */}
                        <div className="flex items-center justify-end mb-4 space-x-4">
                            <div className="flex items-center space-x-2">
                                <div className="w-3 h-3 bg-yellow-300 rounded-full"></div>
                                <span className="text-sm text-gray-600">Series 1</span>
                            </div>
                            <div className="flex items-center space-x-2">
                                <div className="w-3 h-3 bg-gray-300 rounded-full"></div>
                                <span className="text-sm text-gray-600">Series 2</span>
                            </div>
                            <div className="flex items-center space-x-2">
                                <div className="w-3 h-3 bg-teal-600 rounded-full"></div>
                                <span className="text-sm text-gray-600">Series 3</span>
                            </div>
                        </div>

                        {/* Chart Container */}
                        {chartData && chartData.length > 0 && (
                            <div className="h-64">
                                <ChartContainer config={chartConfig} className="h-full w-full">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <LineChart
                                            data={chartData}
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
                                            <Line
                                                type="monotone"
                                                dataKey="series1"
                                                stroke="#E8F4B8"
                                                strokeWidth={3}
                                                dot={{ fill: "#E8F4B8", strokeWidth: 2, r: 4 }}
                                            />
                                            <Line
                                                type="monotone"
                                                dataKey="series2"
                                                stroke="#A8C97F"
                                                strokeWidth={3}
                                                dot={{ fill: "#A8C97F", strokeWidth: 2, r: 4 }}
                                            />
                                            <Line
                                                type="monotone"
                                                dataKey="series3"
                                                stroke="#1D9A8A"
                                                strokeWidth={3}
                                                dot={{ fill: "#1D9A8A", strokeWidth: 2, r: 4 }}
                                            />
                                        </LineChart>
                                    </ResponsiveContainer>
                                </ChartContainer>
                            </div>
                        )}
                    </div>

                    {/* Bullet Points Section */}
                    <div className="px-8 pb-6 space-y-4 mt-10">
                        {bulletPoints && bulletPoints.length > 0 && (
                            <>
                                {bulletPoints.map((point, index) => (
                                    <div key={index} className="flex items-start space-x-4">
                                        <div className={`w-6 h-6 ${getColorClass(point.color)} rounded-full flex-shrink-0 mt-1`}></div>
                                        <p className="text-base leading-relaxed text-gray-700">
                                            {point.text}
                                        </p>
                                    </div>
                                ))}
                            </>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default StatisticSlide; 