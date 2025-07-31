import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(25)
        .default("OUR SOLUTIONS")
        .meta({
            description: "Main section heading - can be 'Solutions', 'Our Approach', 'How We Help', or similar",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(50)
        .default("COMPREHENSIVE SOLUTIONS FOR YOUR NEEDS")
        .meta({
            description: "Supporting subtitle that frames the solution discussion",
        }),

    solutionItems: z.array(z.object({
        itemNumber: z.string().min(1).max(3),
        solutionTitle: z.string().min(5).max(40),
        solutionDescription: z.string().min(20).max(200)
    })).min(2).max(3).default([
        {
            itemNumber: "01",
            solutionTitle: "Process Optimization",
            solutionDescription: "Streamline workflows and implement efficient systems that reduce waste, improve productivity, and maximize resource utilization across all operational areas."
        },
        {
            itemNumber: "02",
            solutionTitle: "Scalable Infrastructure",
            solutionDescription: "Build robust, flexible systems and methodologies that can grow with your organization, eliminating bottlenecks and supporting expansion efforts."
        },
        {
            itemNumber: "03",
            solutionTitle: "Resource Management",
            solutionDescription: "Strategic allocation and optimization of available resources including time, budget, and personnel to achieve maximum impact and desired outcomes."
        }
    ]).meta({
        description: "List of key solutions or approaches with numbered identification and detailed descriptions",
    }),

    primaryVisual: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1560472354-b33ff0c44a43?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Modern workspace with team collaboration and strategic planning"
    }).meta({
        description: "Primary visual representing teamwork, strategy, or solution implementation",
    }),

    brandingVisual: ImageSchema.default({
        __image_url__: "https://via.placeholder.com/150x80/22C55E/FFFFFF?text=LOGO",
        __image_prompt__: "Organization logo or brand mark"
    }).meta({
        description: "Logo or branding element to maintain visual identity",
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

    showColorBlocks: z.boolean()
        .default(true)
        .meta({
            description: "Whether to show colored background blocks for visual hierarchy",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const SolutionsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, solutionItems, primaryVisual, brandingVisual, showYellowUnderline, showVisualAccents, showColorBlocks } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Images and Branding */}
                <div className="w-2/5 relative bg-gray-100 flex flex-col">
                    {/* Top Image Area */}
                    {primaryVisual?.__image_url__ && (
                        <div className="flex-1 relative">
                            <img
                                src={primaryVisual.__image_url__}
                                alt={primaryVisual.__image_prompt__}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    )}

                    {/* Bottom Branding Area */}
                    <div className="h-24 bg-white flex items-center justify-center px-8">
                        {brandingVisual?.__image_url__ && (
                            <img
                                src={brandingVisual.__image_url__}
                                alt={brandingVisual.__image_prompt__}
                                className="h-12 object-contain"
                            />
                        )}
                    </div>
                </div>

                {/* Right Side - Content */}
                <div className="w-3/5 relative bg-teal-600 px-16 py-12 flex flex-col justify-start">
                    {/* Title Section */}
                    <div className="mb-8">
                        {sectionTitle && (
                            <h1 className="text-3xl lg:text-4xl font-black text-white leading-tight mb-4">
                                {sectionTitle}
                            </h1>
                        )}

                        {sectionSubtitle && (
                            <p className="text-base font-semibold text-gray-100 tracking-wide mb-4">
                                {sectionSubtitle}
                            </p>
                        )}

                        {/* Yellow Decorative Underline */}
                        {showYellowUnderline && (
                            <div className="w-24 h-1 bg-yellow-300"></div>
                        )}
                    </div>

                    {/* Solution Items List */}
                    {solutionItems && solutionItems.length > 0 && (
                        <div className="space-y-8">
                            {solutionItems.map((item, index) => (
                                <div key={index} className="flex items-start space-x-6">
                                    {/* Number Circle */}
                                    <div className="w-16 h-16 bg-yellow-300 rounded-full flex items-center justify-center flex-shrink-0">
                                        <span className="text-teal-700 font-bold text-xl">
                                            {item.itemNumber}
                                        </span>
                                    </div>

                                    {/* Content */}
                                    <div className="flex-1">
                                        <h3 className="text-xl font-bold text-white mb-3">
                                            {item.solutionTitle}
                                        </h3>
                                        <p className="text-base leading-relaxed text-gray-100">
                                            {item.solutionDescription}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Visual Accents */}
                    {showVisualAccents && (
                        <>
                            {/* Decorative circles */}
                            <div className="absolute top-8 right-8 w-4 h-4 bg-yellow-300 rounded-full"></div>
                            <div className="absolute bottom-16 right-12 w-3 h-3 bg-yellow-200 rounded-full"></div>
                        </>
                    )}
                </div>
            </div>

            {/* Color blocks for visual hierarchy */}
            {showColorBlocks && (
                <>
                    {/* Bottom accent strip */}
                    <div className="absolute bottom-0 left-0 right-0 h-3 bg-yellow-300"></div>
                    {/* Side accent */}
                    <div className="absolute top-0 left-0 bottom-0 w-1 bg-yellow-400"></div>
                </>
            )}
        </div>
    );
};

export default SolutionsSlide; 