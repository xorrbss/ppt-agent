import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer } from "recharts";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("BUSINESS MODEL")
        .meta({
            description: "Main title for the business model section",
        }),

    subtitle: z.string()
        .min(10)
        .max(25)
        .default("OUR BUSINESS MODEL")
        .meta({
            description: "Subtitle describing the business model",
        }),

    description: z.string()
        .min(50)
        .max(300)
        .default("Our business model thrives on delivering value through strategic innovation, client-centric solutions, and a dynamic blend of creativity and analytics.")
        .meta({
            description: "Description of the business model",
        }),

    businessImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1551288049-bebda4e38f71?ixlib=rb-4.0.3&auto=format&fit=crop&w=1200&q=80",
        __image_prompt__: "Business professionals analyzing charts and data on tablet and computer"
    }).meta({
        description: "Business analytics header image",
    }),

    chartData: z.array(z.object({
        name: z.string(),
        series1: z.number(),
        series2: z.number(),
        series3: z.number()
    })).min(3).max(6).default([
        { name: "Item 1", series1: 5, series2: 4, series3: 3 },
        { name: "Item 2", series1: 8, series2: 7, series3: 4 },
        { name: "Item 3", series1: 15, series2: 10, series3: 5 },
        { name: "Item 4", series1: 18, series2: 15, series3: 8 },
        { name: "Item 5", series1: 22, series2: 20, series3: 8 }
    ]).meta({
        description: "Chart data for business metrics",
    }),

    showYellowAccent: z.boolean()
        .default(true)
        .meta({
            description: "Show yellow accent decoration",
        }),

    showTealAccents: z.boolean()
        .default(true)
        .meta({
            description: "Show teal accent decorations",
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
        color: "#E8F4B8",
    },
    series3: {
        label: "Series 3",
        color: "#A8C97F",
    },
};

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const BusinessModelSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, description, businessImage, chartData, showYellowAccent, showTealAccents } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Header Image with Teal Decorative Elements */}
            <div className="relative h-1/3">
                {/* Business Image */}
                {businessImage?.__image_url__ && (
                    <div className="absolute inset-0">
                        <img
                            src={businessImage.__image_url__}
                            alt={businessImage.__image_prompt__}
                            className="w-full h-full object-cover"
                        />
                    </div>
                )}

                {/* Teal Decorative Accents */}
                {showTealAccents && (
                    <>
                        {/* Top left teal block */}
                        <div className="absolute top-0 left-0 w-20 h-full bg-teal-600 z-10"></div>
                        {/* Top right teal block */}
                        <div className="absolute top-0 right-0 w-32 h-full bg-teal-600 z-10"></div>
                    </>
                )}

                {/* Yellow Accent */}
                {showYellowAccent && (
                    <div className="absolute top-0 right-32 w-24 h-16 bg-yellow-300 z-15"></div>
                )}
            </div>

            {/* Content Section */}
            <div className="h-2/3 flex">
                {/* Left Side - Title and Description */}
                <div className="w-1/2 px-16 py-8 flex flex-col justify-center bg-gray-50">
                    {/* Title Section */}
                    <div className="mb-6">
                        {mainTitle && (
                            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                {mainTitle}
                            </h1>
                        )}

                        {subtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide mb-6">
                                {subtitle}
                            </p>
                        )}
                    </div>

                    {/* Description */}
                    {description && (
                        <div>
                            <p className="text-base leading-relaxed text-gray-700">
                                {description}
                            </p>
                        </div>
                    )}
                </div>

                {/* Right Side - Chart */}
                <div className="w-1/2 px-8 py-8 flex flex-col justify-center bg-white">
                    {/* Chart Legend */}
                    <div className="flex items-center justify-end mb-4 space-x-4">
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-teal-600 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 1</span>
                        </div>
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-yellow-200 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 2</span>
                        </div>
                        <div className="flex items-center space-x-2">
                            <div className="w-3 h-3 bg-green-400 rounded-full"></div>
                            <span className="text-sm text-gray-600">Series 3</span>
                        </div>
                    </div>

                    {/* Chart Container */}
                    {chartData && chartData.length > 0 && (
                        <div className="flex-1">
                            <ChartContainer config={chartConfig} className="h-full w-full">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart
                                        data={chartData}
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
                                            barSize={20}
                                        />
                                        <Bar
                                            dataKey="series2"
                                            fill="#E8F4B8"
                                            radius={[2, 2, 0, 0]}
                                            barSize={20}
                                        />
                                        <Bar
                                            dataKey="series3"
                                            fill="#A8C97F"
                                            radius={[2, 2, 0, 0]}
                                            barSize={20}
                                        />
                                    </BarChart>
                                </ResponsiveContainer>
                            </ChartContainer>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default BusinessModelSlide; 