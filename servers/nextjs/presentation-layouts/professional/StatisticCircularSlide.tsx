import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(20)
        .default("CLIENT SATISFACTION")
        .meta({
            description: "Main section heading - adapt to presentation topic (e.g., 'Climate Progress', 'Treatment Success', 'Learning Achievement', 'Project Completion')",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(35)
        .default("MEASURING OUR IMPACT AND SUCCESS")
        .meta({
            description: "Supporting subtitle that provides context - adapt to topic (e.g., 'Tracking Climate Action Progress', 'Monitoring Patient Recovery Rates', 'Assessing Educational Outcomes')",
        }),

    description: z.string()
        .min(2)
        .max(230)
        .default("At the heart of our success lies the unwavering satisfaction of our clients. We take pride in fostering lasting partnerships, consistently exceeding expectations, and delivering results that not only meet but surpass the unique objectives of each client we serve.")
        .meta({
            description: "Name of the organization or entity being measured",
        }),

    brandLogo: ImageSchema.default({
        image_url_: "https://via.placeholder.com/40x40/22C55E/FFFFFF?text=L",
        image_prompt_: "Professional organization logo - clean and modern design"
    }).meta({
        description: "Logo or brand mark representing the organization",
    }),

    satisfactionRate: z.object({
        value: z.number().min(0).max(100),
        label: z.string().min(5).max(30),
        percentage: z.string().min(2).max(5)
    }).default({
        value: 90,
        label: "CLIENT'S REPEAT ORDER",
        percentage: "90%"
    }).meta({
        description: "CRITICAL: Provide topic-specific circular progress metric. For global warming: {value: 33, label: 'CO2 REDUCTION ACHIEVED', percentage: '33%'} or {value: 78, label: 'RENEWABLE ENERGY ADOPTION', percentage: '78%'}. For healthcare: {value: 95, label: 'PATIENT RECOVERY RATE', percentage: '95%'} or {value: 87, label: 'TREATMENT SUCCESS RATE', percentage: '87%'}. For education: {value: 92, label: 'GRADUATION SUCCESS RATE', percentage: '92%'}. Use realistic percentages and meaningful labels.",
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
        description: "ESSENTIAL: Provide two topic-relevant supporting statistics. For global warming: [{percentage: '1.1°C', description: 'Global temperature increase since pre-industrial times represents urgent need for climate action', backgroundColor: 'teal'}, {percentage: '410ppm', description: 'Current atmospheric CO2 levels are the highest in human history requiring immediate intervention', backgroundColor: 'beige'}]. For healthcare: [{percentage: '85%', description: 'Early detection rates have improved significantly with advanced screening technologies', backgroundColor: 'teal'}, {percentage: '72h', description: 'Average patient response time demonstrates our commitment to rapid care delivery', backgroundColor: 'beige'}]. Always provide factual, impactful statistics.",
    }),

    companyLogo: ImageSchema.default({
        image_url_: "https://via.placeholder.com/40x40/FFFFFF/1D9A8A?text=C",
        image_prompt_: "Clean modern company logo icon in white"
    }).meta({
        description: "Company logo icon",
    }),

    companyName: z.string()
        .min(2)
        .max(25)
        .default("Deskpro")
        .meta({
            description: "Company name for branding",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const StatisticCircularSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, description, brandLogo, satisfactionRate, statisticBlocks, companyLogo, companyName } = data;

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
    const strokeDashoffset = circumference - (circumference * (satisfactionRate?.value || 90) / 100);

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex flex-col">
                {/* Header Section */}
                <div className="flex h-32">
                    {/* Left - Title */}
                    <div className="w-1/2 px-16 py-8 bg-gray-100 flex flex-col justify-center">
                        {sectionTitle && (
                            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-2">
                                {sectionTitle}
                            </h1>
                        )}

                        {sectionSubtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide">
                                {sectionSubtitle}
                            </p>
                        )}
                    </div>

                    {/* Right - Company Branding */}
                    <div className="w-1/2 bg-teal-600 px-16 py-8 flex items-center justify-end">
                        <div className="flex items-center space-x-3">
                            {companyLogo?.image_url_ && (
                                <div className="w-10 h-10">
                                    <img
                                        src={companyLogo.image_url_}
                                        alt={companyLogo.image_prompt_}
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
                                {satisfactionRate?.label && (
                                    <p className="text-sm font-semibold text-gray-800 mb-4 text-center max-w-32 leading-tight">
                                        {satisfactionRate.label}
                                    </p>
                                )}
                                {satisfactionRate?.percentage && (
                                    <span className="text-7xl font-black text-teal-700">
                                        {satisfactionRate.percentage}
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