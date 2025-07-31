import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({


    sectionTitle: z.string()
        .min(3)
        .max(25)
        .default("ABOUT US")
        .meta({
            description: "Main section heading - can be used for any organizational introduction",
        }),

    sectionSubtitle: z.string()
        .min(5)
        .max(40)
        .default("GET TO KNOW US BETTER")
        .meta({
            description: "Supporting subtitle that invites audience engagement and builds connection",
        }),

    organizationDescription: z.string()
        .min(50)
        .max(300)
        .default("We believe in the transformative power of innovation, strategic thinking, and cutting-edge solutions. Our mission is simple: to empower organizations with comprehensive strategies that not only elevate performance but also drive tangible growth and success.")
        .meta({
            description: "Primary description of the organization's mission, values, and approach",
        }),

    additionalContext: z.string()
        .min(30)
        .max(150)
        .default("What sets us apart is not just our expertise but our commitment to understanding the unique needs of each client.")
        .meta({
            description: "Additional context or differentiating statement about the organization",
        }),

    featuredImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Professional business team analyzing data and working collaboratively"
    }).meta({
        description: "Primary visual that represents the organization's work or environment",
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

    showAccentSquare: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display the accent square decoration element",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definitionz
const AboutUsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, organizationDescription, additionalContext, featuredImage, showVisualAccents, showColorBlocks, showAccentSquare } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Content */}
                <div className="w-3/5 relative bg-white px-16 py-12 flex flex-col justify-center">
                    {/* Title Section */}
                    <div className="mb-0">
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

                        {/* Decorative gray line */}
                        <div className="w-1 h-20 bg-gray-400 mb-8"></div>
                    </div>

                    {/* Description Text */}
                    {organizationDescription && (
                        <p className="text-base leading-relaxed text-gray-700 max-w-xl">
                            {organizationDescription}
                        </p>

                    )}

                    {/* Additional Text */}
                    {additionalContext && (
                        <div>
                            <p className="text-base leading-relaxed text-gray-700 max-w-xl">
                                {additionalContext}
                            </p>
                        </div>
                    )}
                </div>

                {/* Right Side - Image and Decorative Elements */}
                <div className="w-2/5 relative">
                    {/* Yellow Square - Top Right */}
                    {showAccentSquare && (
                        <div className="absolute bottom-0 right-0 w-24 h-24 bg-yellow-300 z-10"></div>
                    )}

                    {/* Decorative Circle - On Yellow Square */}
                    {showVisualAccents && (
                        <div className="absolute top-6 right-6 w-6 h-6 border-2 border-teal-600 rounded-full z-20"></div>
                    )}

                    {/* Business Image - Left positioned */}
                    {featuredImage?.__image_url__ && (
                        <div className="absolute right-36 top-1/2 -translate-y-1/2 h-[500px] w-[350px] z-20 shadow-lg">
                            <img
                                src={featuredImage.__image_url__}
                                alt={featuredImage.__image_prompt__}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    )}

                    {/* Teal Accent Areas */}
                    {showColorBlocks && (
                        <>
                            {/* Vertical Teal Strip - Center */}
                            <div className="absolute top-0 right-0 bottom-16 h-full w-[300px] bg-teal-600 z-10"></div>

                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default AboutUsSlide; 