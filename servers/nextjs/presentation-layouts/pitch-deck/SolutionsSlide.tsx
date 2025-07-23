import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("SOLUTIONS")
        .meta({
            description: "Main title for the solutions section",
        }),

    subtitle: z.string()
        .min(10)
        .max(25)
        .default("SOLUTIONS OF THE PROBLEMS")
        .meta({
            description: "Subtitle describing the section",
        }),

    solutions: z.array(z.object({
        number: z.string().min(1).max(3),
        title: z.string().min(5).max(40),
        description: z.string().min(20).max(300)
    })).min(2).max(3).default([
        {
            number: "01",
            title: "Lack of Brand Visibility",
            description: "By defining your unique value proposition and creating a consistent brand identity, we ensure your business stands out and remains memorable in the minds of your target audience."
        },
        {
            number: "02",
            title: "Ineffective Digital Presence",
            description: "Through data-driven insights, we tailor strategies to maximize online visibility, engage your audience, and drive meaningful interactions, converting online engagements into tangible business outcomes."
        },
        {
            number: "03",
            title: "Lack of Targeted Lead Generation",
            description: "By leveraging strategic content, paid advertising, and personalized engagement tactics, we ensure that your marketing efforts are focused on reaching and converting the right audience."
        }
    ]).meta({
        description: "List of solutions with numbers, titles and descriptions",
    }),

    workspaceImages: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1551288049-bebda4e38f71?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Business person working on laptop with charts and analytics"
    }).meta({
        description: "Two workspace images for left side",
    }),

    companyLogo: ImageSchema.default({
        __image_url__: "https://via.placeholder.com/40x40/1D9A8A/FFFFFF?text=C",
        __image_prompt__: "Clean modern company logo icon"
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

    showYellowUnderline: z.boolean()
        .default(true)
        .meta({
            description: "Show yellow decorative underline",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const SolutionsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, solutions, workspaceImages, companyLogo, companyName, showYellowUnderline } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Images and Branding */}
                <div className="w-1/2 relative bg-gray-100 px-16 py-12 flex flex-col">
                    {/* Title Section */}
                    <div className="mb-8">
                        {mainTitle && (
                            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                {mainTitle}
                            </h1>
                        )}

                        {subtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide mb-4">
                                {subtitle}
                            </p>
                        )}

                        {/* Yellow Decorative Underline */}
                        {showYellowUnderline && (
                            <div className="w-24 h-1 bg-yellow-300"></div>
                        )}
                    </div>

                    {/* Images */}
                    {workspaceImages && (
                        <div className="flex-1 space-y-6">



                            <div className="h-full w-full">
                                <img
                                    src={workspaceImages.__image_url__}
                                    alt={workspaceImages.__image_prompt__}
                                    className="w-full h-full object-cover rounded-lg shadow-md"
                                />
                            </div>
                        </div>
                    )}

                    {/* Company Branding */}
                    <div className="mt-8 flex items-center space-x-3">
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
                            <span className="text-xl font-bold text-teal-700">
                                {companyName}
                            </span>
                        )}
                    </div>
                </div>

                {/* Right Side - Teal Background with Solutions */}
                <div className="w-1/2 relative bg-teal-600 px-16 py-12 flex flex-col justify-center">
                    {/* Solutions List */}
                    {solutions && solutions.length > 0 && (
                        <div className="space-y-8">
                            {solutions.map((solution, index) => (
                                <div key={index} className="flex items-start space-x-4">
                                    {/* Number Circle */}
                                    <div className="w-12 h-12 bg-yellow-200 rounded-full flex items-center justify-center flex-shrink-0">
                                        <span className="text-teal-700 font-bold text-lg">
                                            {solution.number}
                                        </span>
                                    </div>

                                    {/* Content */}
                                    <div className="flex-1">
                                        <h3 className="text-xl font-bold text-white mb-3">
                                            {solution.title}
                                        </h3>
                                        <p className="text-base leading-relaxed text-white">
                                            {solution.description}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default SolutionsSlide; 