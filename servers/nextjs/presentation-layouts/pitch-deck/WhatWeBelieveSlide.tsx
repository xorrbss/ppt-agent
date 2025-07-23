import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({


    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("WHAT WE BELIEVE")
        .meta({
            description: "Main title for the beliefs section",
        }),

    subtitle: z.string()
        .min(10)
        .max(30)
        .default("ABOUT OUR VISION AND MISSION")
        .meta({
            description: "Subtitle describing the section",
        }),

    visionTitle: z.string()
        .min(3)
        .max(15)
        .default("VISION")
        .meta({
            description: "Vision section title",
        }),

    visionText: z.string()
        .min(50)
        .max(300)
        .default("Our vision is to be the catalyst for transformative marketing solutions that redefine industry standards. We envision a future where brands not only captivate their audience but also inspire meaningful connections.")
        .meta({
            description: "Vision statement text",
        }),

    missionTitle: z.string()
        .min(3)
        .max(15)
        .default("MISSION")
        .meta({
            description: "Mission section title",
        }),

    missionText: z.string()
        .min(50)
        .max(400)
        .default("Our mission is to deliver strategic and impactful marketing solutions that propel businesses to new heights of success. We are committed to leveraging our expertise in data-driven insights, creative storytelling, and cutting-edge technology to craft bespoke campaigns.")
        .meta({
            description: "Mission statement text",
        }),

    teamImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Business team collaboration meeting with documents and discussion"
    }).meta({
        description: "Team collaboration image",
    }),

    showYellowAccent: z.boolean()
        .default(true)
        .meta({
            description: "Show yellow accent block",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const WhatWeBelieveSlide = ({ data }: { data: Partial<SchemaType> }) => {


    const { mainTitle, subtitle, visionTitle, visionText, missionTitle, missionText, teamImage, showYellowAccent } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Image */}
                <div className="w-1/2 relative">
                    {/* Team Image */}
                    {teamImage?.__image_url__ && (
                        <div className="absolute top-0 left-0 bottom-32 right-0">
                            <img
                                src={teamImage.__image_url__}
                                alt={teamImage.__image_prompt__}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    )}

                    {/* Yellow Accent Block - Bottom Left */}
                    {showYellowAccent && (
                        <div className="absolute bottom-0 left-0 w-32 h-32 bg-yellow-300 z-10"></div>
                    )}

                    {/* Teal Accent Block - Bottom */}
                    <div className="absolute bottom-0 left-32 right-0 h-32 bg-teal-600 z-10"></div>
                </div>

                {/* Right Side - Content */}
                <div className="w-1/2 relative bg-white  ">
                    <div className="px-16 py-12">

                        {/* Title Section */}
                        <div className="mb-8">
                            {mainTitle && (
                                <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                    {mainTitle}
                                </h1>
                            )}

                            {subtitle && (
                                <p className="text-base font-semibold text-gray-800 tracking-wide mb-8">
                                    {subtitle}
                                </p>
                            )}
                        </div>

                        {/* Vision Section */}
                        <div className="mb-8">
                            {visionTitle && (
                                <h2 className="text-2xl font-bold text-teal-700 mb-4">
                                    {visionTitle}
                                </h2>
                            )}
                            {visionText && (
                                <p className="text-base leading-relaxed text-gray-700">
                                    {visionText}
                                </p>
                            )}
                        </div>

                    </div>
                    {/* Mission Section with Teal Background */}
                    <div className="bg-teal-600 p-6 absolute bottom-0 left-0 right-0 px-16 text-white">
                        {missionTitle && (
                            <h2 className="text-2xl font-bold mb-4">
                                {missionTitle}
                            </h2>
                        )}
                        {missionText && (
                            <p className="text-base leading-relaxed">
                                {missionText}
                            </p>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default WhatWeBelieveSlide; 