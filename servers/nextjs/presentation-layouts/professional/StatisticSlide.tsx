import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { LineChart, Line, XAxis, YAxis, CartesianGrid } from "recharts";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("KEY STATISTICS")
        .meta({
            description: "Main section heading - adapt to presentation topic (e.g., 'Climate Data', 'Health Metrics', 'Performance Stats', 'Research Findings')",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(60)
        .default("DATA-DRIVEN INSIGHTS AND PERFORMANCE")
        .meta({
            description: "Supporting subtitle that frames the data - adapt to topic (e.g., 'Global Temperature Trends and Impact', 'Patient Outcomes and Recovery Rates', 'Student Achievement and Progress')",
        }),

    statisticValue: z.string()
        .min(1)
        .max(15)
        .default("85%")
        .meta({
            description: "CRITICAL: Provide the most important statistic for the topic. For global warming: '1.1°C', '+2.1°C', '410ppm', '33%'. For healthcare: '95%', '72 hours', '89%'. For education: '78%', '3.2 GPA', '92%'. Use real, impactful numbers relevant to the presentation topic.",
        }),

    statisticLabel: z.string()
        .min(5)
        .max(40)
        .default("Client Satisfaction Rate")
        .meta({
            description: "IMPORTANT: Provide topic-specific label for the main statistic. For global warming: 'Global Temperature Rise Since 1880', 'CO2 Concentration Increase', 'Arctic Ice Loss Rate'. For healthcare: 'Patient Recovery Rate', 'Treatment Success Rate', 'Early Detection Rate'. For education: 'Graduation Success Rate', 'Student Engagement Level', 'Learning Improvement Rate'.",
        }),

    supportingVisual: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1460925895917-afdab827c52f?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Business analytics dashboard with charts and data visualization"
    }).meta({
        description: "ADAPT the image prompt to match the presentation topic: For global warming: 'Climate monitoring station with temperature sensors and weather equipment', 'Scientists analyzing ice core data in Arctic research facility'. For healthcare: 'Medical monitoring equipment displaying patient vital signs', 'Healthcare analytics dashboard showing treatment outcomes'. For education: 'Educational assessment data on computer screens', 'Students using digital learning platforms'.",
    }),

    bulletPoints: z.array(z.string().min(10).max(100)).min(2).max(5).default([
        "Consistent performance improvement over 12 months",
        "High customer retention and satisfaction scores",
        "Measurable ROI across all key performance indicators",
        "Data-driven decision making and strategic optimization"
    ]).meta({
        description: "ESSENTIAL: Provide topic-relevant supporting facts and insights. For global warming: 'Global average temperature has risen 1.1°C since pre-industrial times', 'Arctic sea ice is declining at 13% per decade', 'CO2 levels are highest in 3 million years', 'Renewable energy adoption increased 85% in last decade'. For healthcare: 'Early detection improves survival rates by 85%', 'Telemedicine reduced patient wait times by 60%', 'Preventive care decreased hospital readmissions by 40%'. Always provide factual, verifiable statements related to the presentation topic.",
    }),

    chartData: z.array(z.object({
        name: z.string(),
        series1: z.number(),
        series2: z.number(),
        series3: z.number()
    })).min(5).max(5).default([
        { name: "Jan", series1: 18, series2: 0, series3: 0 },
        { name: "Feb", series1: 30, series2: 12, series3: 8 },
        { name: "Mar", series1: 26, series2: 38, series3: 20 },
        { name: "Apr", series1: 40, series2: 30, series3: 35 },
        { name: "May", series1: 42, series2: 45, series3: 32 }
    ]).meta({
        description: "CRITICAL: Provide topic-specific time-series data for line chart. For global warming: Monthly temperature anomalies, CO2 levels, ice coverage data with realistic values. For healthcare: Patient recovery rates, treatment success metrics, diagnostic accuracy over time. For education: Student performance trends, learning progress, engagement metrics. Use realistic data patterns showing meaningful trends.",
    }),

    showYellowUnderline: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display the decorative yellow underline accent",
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

    const { sectionTitle, sectionSubtitle, statisticValue, statisticLabel, supportingVisual, bulletPoints, chartData, showYellowUnderline, showVisualAccents } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Teal Background */}
                <div className="w-1/2 relative bg-teal-600 px-16 py-12 flex flex-col text-white">
                    {/* Title Section */}
                    <div className="mb-8">
                        {sectionTitle && (
                            <h1 className="text-3xl lg:text-4xl font-black leading-tight mb-4">
                                {sectionTitle}
                            </h1>
                        )}

                        {sectionSubtitle && (
                            <p className="text-base font-semibold tracking-wide mb-4">
                                {sectionSubtitle}
                            </p>
                        )}

                        {/* Yellow Decorative Underline */}
                        {showYellowUnderline && (
                            <div className="w-24 h-1 bg-yellow-300 mb-8"></div>
                        )}
                    </div>

                    {/* Large Statistic Display */}
                    <div className="mb-8">
                        {statisticValue && (
                            <div className="text-8xl font-black text-yellow-300 mb-4">
                                {statisticValue}
                            </div>
                        )}
                        {statisticLabel && (
                            <h2 className="text-2xl font-bold">
                                {statisticLabel}
                            </h2>
                        )}
                    </div>

                    {/* Business Image */}
                    {supportingVisual?.__image_url__ && (
                        <div className="flex-1 flex items-end">
                            <div className="w-full h-48">
                                <img
                                    src={supportingVisual.__image_url__}
                                    alt={supportingVisual.__image_prompt__}
                                    className="w-full h-full object-cover rounded-lg"
                                />
                            </div>
                        </div>
                    )}

                    {/* Visual Accents */}
                    {showVisualAccents && (
                        <>
                            <div className="absolute top-8 right-8 w-6 h-6 bg-yellow-300 rounded-full"></div>
                            <div className="absolute bottom-12 left-8 w-4 h-4 bg-yellow-200 rounded-full"></div>
                        </>
                    )}
                </div>

                {/* Right Side - White Background with Chart and Bullet Points */}
                <div className="w-1/2 relative bg-white px-16 py-12">
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

                                </ChartContainer>
                            </div>
                        )}
                    </div>

                    {/* Bullet Points Section */}
                    <div className="px-8 pb-6 space-y-4 mt-10">
                        {bulletPoints && bulletPoints.length > 0 && (
                            <>
                                {bulletPoints.map((point, index) => {
                                    // Rotate colors for visual variety
                                    const colors = ['bg-teal-600', 'bg-yellow-300', 'bg-gray-400'];
                                    const dotColor = colors[index % colors.length];

                                    return (
                                        <div key={index} className="flex items-start space-x-4">
                                            <div className={`w-6 h-6 ${dotColor} rounded-full flex-shrink-0 mt-1`}></div>
                                            <p className="text-base leading-relaxed text-gray-700">
                                                {point}
                                            </p>
                                        </div>
                                    );
                                })}
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* Bottom accent strip */}
            <div className="absolute bottom-0 left-0 right-0 h-3 bg-yellow-300"></div>
        </div>
    );
};

export default StatisticSlide; 