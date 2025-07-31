import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("OUR VISION & MISSION")
        .meta({
            description: "Main section heading - can be 'Our Values', 'What We Believe', 'Our Philosophy', or similar",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(60)
        .default("GUIDING PRINCIPLES AND CORE BELIEFS")
        .meta({
            description: "Supporting subtitle that introduces the organization's foundational concepts",
        }),

    visionStatement: z.string()
        .min(30)
        .max(200)
        .default("We envision a future where innovative solutions transform challenges into opportunities, creating sustainable value for all stakeholders.")
        .meta({
            description: "Vision statement describing the organization's aspirational future goals and impact",
        }),

    missionContent: z.object({
        missionTitle: z.string().min(3).max(30).default("Our Mission"),
        missionDescription: z.string().min(50).max(300).default("To deliver exceptional value through strategic innovation, collaborative partnerships, and unwavering commitment to excellence. We believe in empowering organizations with the tools, insights, and support needed to achieve sustainable growth and meaningful impact in their communities.")
    }).default({
        missionTitle: "Our Mission",
        missionDescription: "To deliver exceptional value through strategic innovation, collaborative partnerships, and unwavering commitment to excellence. We believe in empowering organizations with the tools, insights, and support needed to achieve sustainable growth and meaningful impact in their communities."
    }).meta({
        description: "Mission section with title and detailed description of organizational purpose and approach",
    }),

    supportingVisual: ImageSchema.default({
        image_url_: "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        image_prompt_: "Diverse team collaborating and planning together in modern workspace"
    }).meta({
        description: "Visual that represents collaboration, vision, or organizational culture",
    }),

    showVisualAccents: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display decorative visual accent elements",
        }),

    showColorBlocks: z.boolean()
        .default(true)
        .meta({
            description: "Whether to show colored background sections for visual hierarchy",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const WhatWeBelieveSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, visionStatement, missionContent, supportingVisual, showVisualAccents, showColorBlocks } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Image */}
                <div className="w-2/5 relative">
                    {supportingVisual?.image_url_ && (
                        <div className="absolute inset-8 shadow-lg">
                            <img
                                src={supportingVisual.image_url_}
                                alt={supportingVisual.image_prompt_}
                                className="w-full h-full object-cover rounded-lg"
                            />
                        </div>
                    )}

                    {/* Visual Accents */}
                    {showVisualAccents && (
                        <>
                            {/* Decorative circles */}
                            <div className="absolute top-4 right-4 w-6 h-6 bg-teal-600 rounded-full opacity-70 z-20"></div>
                            <div className="absolute bottom-8 left-8 w-4 h-4 bg-yellow-300 rounded-full z-20"></div>
                        </>
                    )}
                </div>

                {/* Right Side - Content */}
                <div className="w-3/5 relative bg-white px-16 py-12 flex flex-col justify-start">
                    {/* Title Section */}
                    <div className="mb-8">
                        {sectionTitle && (
                            <h1 className="text-3xl lg:text-4xl font-black text-teal-700 leading-tight mb-4">
                                {sectionTitle}
                            </h1>
                        )}

                        {sectionSubtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide mb-8">
                                {sectionSubtitle}
                            </p>
                        )}
                    </div>

                    {/* Vision Section */}
                    {visionStatement && (
                        <div className="mb-8">
                            <h2 className="text-xl font-bold text-gray-900 mb-4">Vision</h2>
                            <p className="text-base leading-relaxed text-gray-700">
                                {visionStatement}
                            </p>
                        </div>
                    )}

                    {/* Mission Section with Teal Background */}
                    {missionContent && (
                        <div className="bg-teal-600 px-8 py-6 rounded-lg">
                            {missionContent.missionTitle && (
                                <h2 className="text-xl font-bold text-white mb-4">
                                    {missionContent.missionTitle}
                                </h2>
                            )}
                            {missionContent.missionDescription && (
                                <p className="text-base leading-relaxed text-gray-100">
                                    {missionContent.missionDescription}
                                </p>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Color blocks for visual hierarchy */}
            {showColorBlocks && (
                <>
                    {/* Bottom accent strip */}
                    <div className="absolute bottom-0 left-0 right-0 h-2 bg-teal-600"></div>
                    {/* Left accent */}
                    <div className="absolute top-0 left-0 bottom-0 w-2 bg-yellow-300"></div>
                </>
            )}
        </div>
    );
};

export default WhatWeBelieveSlide; 