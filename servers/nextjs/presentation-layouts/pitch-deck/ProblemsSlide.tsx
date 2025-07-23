import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(20)
        .default("PROBLEMS")
        .meta({
            description: "Main title for the problems section",
        }),

    subtitle: z.string()
        .min(10)
        .max(25)
        .default("WE WILL SOLVE THE PROBLEMS")
        .meta({
            description: "Subtitle describing the section",
        }),

    problems: z.array(z.object({
        number: z.string().min(1).max(3),
        title: z.string().min(5).max(40),
        description: z.string().min(20).max(200)
    })).min(2).max(3).default([
        {
            number: "01",
            title: "Lack of Brand Visibility",
            description: "Many businesses struggle with gaining visibility in a saturated market. Our solution involves a comprehensive analysis of your brand, audience, and competitors, leading to the development of a strategic branding."
        },
        {
            number: "02",
            title: "Ineffective Digital Presence",
            description: "Weak online presence can hinder business growth. Our agency offers an integrated approach to digital marketing, covering SEO optimization, social media management, content marketing, and more."
        },
        {
            number: "03",
            title: "Lack of Targeted Lead Generation",
            description: "Many businesses struggle with generating quality leads that convert into customers. Our solution involves a meticulous understanding of your target audience, allowing us to develop highly targeted lead generation campaigns."
        }
    ]).meta({
        description: "List of problems with numbers, titles and descriptions",
    }),

    workspaceImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1542744173-8e7e53415bb0?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Clean modern workspace with laptop, plant, and documents on desk"
    }).meta({
        description: "Workspace image showing business environment",
    }),

    showDecoCircle: z.boolean()
        .default(true)
        .meta({
            description: "Show decorative circle element",
        }),

    showTealAccent: z.boolean()
        .default(true)
        .meta({
            description: "Show teal accent block",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const ProblemsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, problems, workspaceImage, showDecoCircle, showTealAccent } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Content */}
                <div className="w-3/5 relative bg-white px-16 py-12 flex flex-col justify-start">
                    {/* Title Section */}
                    <div className="mb-12">
                        {mainTitle && (
                            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                {mainTitle}
                            </h1>
                        )}

                        {subtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide">
                                {subtitle}
                            </p>
                        )}
                    </div>

                    {/* Problems List */}
                    {problems && problems.length > 0 && (
                        <div className="space-y-8">
                            {problems.map((problem, index) => (
                                <div key={index} className="flex items-start space-x-6">
                                    {/* Number Circle */}
                                    <div className="w-16 h-16 bg-yellow-200 rounded-full flex items-center justify-center flex-shrink-0">
                                        <span className="text-teal-700 font-bold text-xl">
                                            {problem.number}
                                        </span>
                                    </div>

                                    {/* Content */}
                                    <div className="flex-1">
                                        <h3 className="text-xl font-bold text-gray-900 mb-3">
                                            {problem.title}
                                        </h3>
                                        <p className="text-base leading-relaxed text-gray-700">
                                            {problem.description}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Right Side - Image and Decorative Elements */}
                <div className="w-2/5 relative">
                    {/* Decorative Circle */}
                    {showDecoCircle && (
                        <div className="absolute top-12 left-8 w-8 h-8 border-4 border-teal-600 rounded-full z-20"></div>
                    )}

                    {/* Workspace Image */}
                    {workspaceImage?.__image_url__ && (
                        <div className="absolute top-8 right-8 bottom-20 left-4 z-15 shadow-lg">
                            <img
                                src={workspaceImage.__image_url__}
                                alt={workspaceImage.__image_prompt__}
                                className="w-full h-full object-cover rounded-lg"
                            />
                        </div>
                    )}

                    {/* Teal Accent Block - Right Edge */}
                    {showTealAccent && (
                        <div className="absolute top-0 right-0 bottom-0 w-16 bg-teal-600 z-10"></div>
                    )}
                </div>
            </div>

            {/* Bottom Teal Stripe */}
            <div className="absolute bottom-0 left-0 right-0 h-4 bg-teal-600 z-5"></div>
        </div>
    );
};

export default ProblemsSlide; 