import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid } from "recharts";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("BUSINESS MODEL")
        .meta({
            description: "Main section heading - adapt to presentation topic (e.g., 'Revenue Strategy', 'Funding Model', 'Implementation Plan', 'Solution Framework')",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(60)
        .default("SUSTAINABLE REVENUE AND VALUE CREATION")
        .meta({
            description: "Supporting subtitle that describes the approach - adapt to topic (e.g., 'Carbon Reduction Strategy', 'Healthcare Delivery Model', 'Educational Framework')",
        }),

    modelDescription: z.string()
        .min(50)
        .max(300)
        .default("Our business model focuses on creating sustainable value through multiple revenue streams, strategic partnerships, and customer-centric solutions. We prioritize long-term relationships and scalable growth opportunities.")
        .meta({
            description: "IMPORTANT: Provide topic-specific description of the model/approach. For global warming: describe carbon reduction strategies, renewable energy adoption, sustainability metrics. For healthcare: treatment protocols, patient care models. For education: learning methodologies, curriculum design. Always provide concrete, relevant details for the presentation topic.",
        }),

    headerVisual: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1559136555-9303baea8ebd?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Business strategy meeting with charts, graphs and team collaboration"
    }).meta({
        description: "Header visual representing the topic area - ADAPT the image prompt to match presentation topic (e.g., 'Climate scientists analyzing global warming data', 'Medical team reviewing patient care protocols', 'Teachers planning educational curriculum')",
    }),

    chartData: z.array(z.object({
        category: z.string().min(3).max(25),
        value: z.number().min(0).max(100),
        color: z.string().min(3).max(20).optional()
    })).min(2).max(6).default([
        { category: "Product Sales", value: 45, color: "#22C55E" },
        { category: "Services", value: 30, color: "#0891B2" },
        { category: "Partnerships", value: 15, color: "#FDE047" },
        { category: "Licensing", value: 10, color: "#F97316" }
    ]).meta({
        description: "CRITICAL: Provide actual data relevant to the presentation topic. For global warming: CO2 emission sources (Transport 29%, Energy 25%, Industry 21%, Agriculture 24%), temperature rise by decade, renewable energy adoption rates. For healthcare: treatment success rates, patient demographics, cost breakdowns. For education: student performance metrics, learning outcomes, resource allocation. Always use REAL topic-specific data with appropriate categories and realistic values.",
    }),

    showChart: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display the data visualization - typically keep true for data-driven presentations",
        }),

    showVisualAccents: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display decorative visual accent elements",
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

    const { sectionTitle, sectionSubtitle, modelDescription, headerVisual, chartData, showChart, showVisualAccents } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Header Image Section */}
            {headerVisual?.__image_url__ && (
                <div className="h-32 w-full relative">
                    <img
                        src={headerVisual.__image_url__}
                        alt={headerVisual.__image_prompt__}
                        className="w-full h-full object-cover"
                    />
                    <div className="absolute inset-0 bg-black bg-opacity-40"></div>
                </div>
            )}

            {/* Main Content Area */}
            <div className="flex h-[calc(100%-8rem)]">
                {/* Left Side - Content */}
                <div className="w-1/2 px-16 py-8 flex flex-col justify-start">
                    {/* Title Section */}
                    <div className="mb-6">
                        {sectionTitle && (
                            <h1 className="text-3xl lg:text-4xl font-black text-teal-700 leading-tight mb-4">
                                {sectionTitle}
                            </h1>
                        )}

                        {sectionSubtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide mb-6">
                                {sectionSubtitle}
                            </p>
                        )}
                    </div>

                    {/* Model Description */}
                    {modelDescription && (
                        <div>
                            <p className="text-base leading-relaxed text-gray-700">
                                {modelDescription}
                            </p>
                        </div>
                    )}

                    {/* Visual Accents */}
                    {showVisualAccents && (
                        <>
                            <div className="absolute bottom-8 left-8 w-6 h-6 bg-yellow-300 rounded-full"></div>
                            <div className="absolute top-40 right-1/2 w-4 h-4 bg-teal-600 rounded-full"></div>
                        </>
                    )}
                </div>

                {/* Right Side - Chart */}
                <div className="w-1/2 px-8 py-8 flex flex-col justify-center">
                    {showChart && chartData && chartData.length > 0 && (
                        <div className="h-80 w-full">
                            <ChartContainer
                                config={{
                                    value: {
                                        label: "Value",
                                        color: "hsl(var(--chart-1))",
                                    },
                                }}
                                className="h-full w-full"
                            >

                                <BarChart data={chartData} margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                                    <XAxis
                                        dataKey="category"
                                        stroke="#6b7280"
                                        fontSize={12}
                                        angle={-45}
                                        textAnchor="end"
                                        height={60}
                                    />
                                    <YAxis stroke="#6b7280" fontSize={12} />
                                    <ChartTooltip content={<ChartTooltipContent />} />
                                    <Bar
                                        dataKey="value"
                                        fill="#0891b2"
                                        radius={[4, 4, 0, 0]}
                                    />
                                </BarChart>

                            </ChartContainer>
                        </div>
                    )}
                </div>
            </div>

            {/* Bottom accent strip */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-teal-600"></div>
        </div>
    );
};

export default BusinessModelSlide; 