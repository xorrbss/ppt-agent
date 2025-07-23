import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("STATISTIC")
        .meta({
            description: "Main title for the statistic section",
        }),

    subtitle: z.string()
        .min(10)
        .max(25)
        .default("CLIENT'S SATISFACTION")
        .meta({
            description: "Subtitle describing the statistic focus",
        }),

    description: z.string()
        .min(100)
        .max(400)
        .default("At the heart of our success lies the unwavering satisfaction of our clients. We take pride in fostering lasting partnerships, consistently exceeding expectations, and delivering results that not only meet but surpass the unique objectives of each client we serve.")
        .meta({
            description: "Description of client satisfaction approach",
        }),

    circularMetric: z.object({
        value: z.number().min(0).max(100),
        label: z.string().min(5).max(30),
        percentage: z.string().min(2).max(5)
    }).default({
        value: 90,
        label: "CLIENT'S REPEAT ORDER",
        percentage: "90%"
    }).meta({
        description: "Main circular chart metric",
    }),

    statisticBlocks: z.array(z.object({
        percentage: z.string().min(2).max(5),
        description: z.string().min(20).max(150),
        backgroundColor: z.enum(["teal", "beige"])
    })).min(2).max(2).default([
        {
            percentage: "90%",
            description: "Our client loyalty speaks volumes as evidenced by a robust repeat order rate",
            backgroundColor: "teal"
        },
        {
            percentage: "99%",
            description: "Our paramount focus on client satisfaction is the bedrock of our agency's success.",
            backgroundColor: "beige"
        }
    ]).meta({
        description: "Two statistic blocks with percentages and descriptions",
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
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const StatisticCircularSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, description, circularMetric, statisticBlocks, companyLogo, companyName } = data;

    const getBackgroundClass = (bg: string) => {
        switch (bg) {
            case "teal": return "bg-teal-600 text-white";
            case "beige": return "bg-yellow-200 text-gray-900";
            default: return "bg-gray-200 text-gray-900";
        }
    };

    // Calculate stroke dash array for circular progress
    const radius = 150;
    const circumference = 2 * Math.PI * radius;
    const strokeDasharray = circumference;
    const strokeDashoffset = circumference - (circumference * (circularMetric?.value || 90) / 100);

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex flex-col">
                {/* Header Section */}
                <div className="flex h-32">
                    {/* Left - Title */}
                    <div className="w-1/2 px-16 py-8 bg-gray-100 flex flex-col justify-center">
                        {mainTitle && (
                            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-2">
                                {mainTitle}
                            </h1>
                        )}

                        {subtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide">
                                {subtitle}
                            </p>
                        )}
                    </div>

                    {/* Right - Company Branding */}
                    <div className="w-1/2 bg-teal-600 px-16 py-8 flex items-center justify-end">
                        <div className="flex items-center space-x-3">
                            {companyLogo?.__image_url__ && (
                                <div className="w-10 h-10">
                                    <img
                                        src={companyLogo.__image_url__}
                                        alt={companyLogo.__image_prompt__}
                                        className="w-full h-full object-contain"
                                    />
                                </div>
                            )}
                            {companyName && (
                                <span className="text-xl font-bold text-white">
                                    {companyName}
                                </span>
                            )}
                        </div>
                    </div>
                </div>

                {/* Content Section */}
                <div className="flex-1 flex">
                    {/* Left Side - Circular Chart */}
                    <div className="w-1/2 px-8 py-8 bg-gray-100 flex items-center justify-center">
                        <div className="relative">
                            {/* Circular Progress SVG */}
                            <svg width="340" height="340" className="transform -rotate-90">
                                {/* Background circle */}
                                <circle
                                    cx="170"
                                    cy="170"
                                    r={radius}
                                    stroke="#E5E7EB"
                                    strokeWidth="25"
                                    fill="transparent"
                                />
                                {/* Progress circle */}
                                <circle
                                    cx="170"
                                    cy="170"
                                    r={radius}
                                    stroke="#1D9A8A"
                                    strokeWidth="25"
                                    fill="transparent"
                                    strokeDasharray={strokeDasharray}
                                    strokeDashoffset={strokeDashoffset}
                                    strokeLinecap="round"
                                    className="transition-all duration-1000 ease-out"
                                />
                            </svg>

                            {/* Center Content */}
                            <div className="absolute inset-0 flex flex-col items-center justify-center">
                                {circularMetric?.label && (
                                    <p className="text-sm font-semibold text-gray-800 mb-4 text-center max-w-32 leading-tight">
                                        {circularMetric.label}
                                    </p>
                                )}
                                {circularMetric?.percentage && (
                                    <span className="text-7xl font-black text-teal-700">
                                        {circularMetric.percentage}
                                    </span>
                                )}
                            </div>
                        </div>
                    </div>

                    {/* Right Side - Content and Statistics */}
                    <div className="w-1/2 bg-white flex flex-col">
                        {/* Description */}
                        <div className="px-16 py-8 flex-1 flex items-start justify-center flex-col">
                            {description && (
                                <p className="text-base leading-relaxed text-gray-700">
                                    {description}
                                </p>
                            )}
                        </div>

                        {/* Statistics Blocks */}
                        {statisticBlocks && statisticBlocks.length > 0 && (
                            <div className="px-16 pb-8 space-y-4">
                                {statisticBlocks.map((block, index) => (
                                    <div key={index} className="flex items-stretch">
                                        {/* Percentage Block */}
                                        <div className={`w-24 h-20 ${getBackgroundClass(block.backgroundColor)} flex items-center justify-center flex-shrink-0`}>
                                            <span className="text-2xl font-black">
                                                {block.percentage}
                                            </span>
                                        </div>

                                        {/* Description Block */}
                                        <div className="flex-1 border-2 border-gray-900 p-4 h-20 flex items-center">
                                            <p className="text-sm leading-relaxed text-gray-900">
                                                {block.description}
                                            </p>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default StatisticCircularSlide; 