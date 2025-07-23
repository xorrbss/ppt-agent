import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("MARKET SIZE")
        .meta({
            description: "Main title for the market size section",
        }),

    subtitle: z.string()
        .min(10)
        .max(25)
        .default("OUR CLIENTS COME FROM EVERYWHERE")
        .meta({
            description: "Subtitle describing global reach",
        }),

    globalDescription: z.string()
        .min(50)
        .max(200)
        .default("With a global perspective, our marketing agency has proudly served multinational clients, delivering tailored strategies that transcend borders and cultures, ensuring consistent brand success on a worldwide scale.")
        .meta({
            description: "Description of global market presence",
        }),

    worldMapImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1516245834210-c4c142787335?ixlib=rb-4.0.3&auto=format&fit=crop&w=1200&q=80",
        __image_prompt__: "World map with location pins showing global business presence"
    }).meta({
        description: "World map image showing global reach",
    }),

    marketDefinitions: z.array(z.object({
        acronym: z.string().min(2).max(10),
        fullName: z.string().min(10).max(50),
        description: z.string().min(50).max(300)
    })).min(3).max(3).default([
        {
            acronym: "TAM",
            fullName: "Total Available Market (TAM)",
            description: "The Total Available Market (TAM) represents the entire potential demand for our product or service, reflecting the vast landscape of opportunities awaiting exploration and market capture."
        },
        {
            acronym: "SAM",
            fullName: "Serviceable Available Market (SAM)",
            description: "The Serviceable Available Market (SAM) represents the specific segment of the Total Available Market where our product or service can be realistically and effectively offered, defining the target audience for our strategic market approach."
        },
        {
            acronym: "SOM",
            fullName: "Serviceable Obtainable Market (SOM)",
            description: "The Serviceable Obtainable Market (SOM) signifies the realistic and achievable portion of the Serviceable Available Market where our business aims to capture market share, emphasizing our practical and strategic approach to market penetration."
        }
    ]).meta({
        description: "Market size definitions for TAM, SAM, and SOM",
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
const MarketSizeSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, globalDescription, worldMapImage, marketDefinitions, showYellowUnderline } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Teal Background with Map */}
                <div className="w-1/2 relative bg-teal-600 px-16 py-12 flex flex-col text-white">
                    {/* Title Section */}
                    <div className="mb-8">
                        {mainTitle && (
                            <h1 className="text-5xl lg:text-6xl font-black leading-tight mb-4">
                                {mainTitle}
                            </h1>
                        )}

                        {subtitle && (
                            <p className="text-base font-semibold tracking-wide mb-4">
                                {subtitle}
                            </p>
                        )}

                        {/* Yellow Decorative Underline */}
                        {showYellowUnderline && (
                            <div className="w-24 h-1 bg-yellow-300 "></div>
                        )}
                    </div>

                    {/* World Map Image */}
                    {worldMapImage?.__image_url__ && (
                        <div className="flex-1 flex items-center justify-center">
                            <div className="w-full max-w-md">
                                <img
                                    src={worldMapImage.__image_url__}
                                    alt={worldMapImage.__image_prompt__}
                                    className="w-full h-auto object-contain opacity-90"
                                />
                            </div>
                        </div>
                    )}

                    {/* Global Description */}
                    {globalDescription && (
                        <div>
                            <p className="text-base leading-relaxed">
                                {globalDescription}
                            </p>
                        </div>
                    )}
                </div>

                {/* Right Side - White Background with Market Definitions */}
                <div className="w-1/2 relative bg-white px-16 py-12 flex flex-col justify-center">
                    {/* Market Definitions */}
                    {marketDefinitions && marketDefinitions.length >= 3 && (
                        <div className="space-y-8">
                            {marketDefinitions.slice(0, 3).map((definition, index) => (
                                <div key={index} className="mb-8">
                                    {/* Header with rounded background */}
                                    <div className="bg-teal-600 text-white px-6 py-3 rounded-full mb-4">
                                        <h3 className="text-lg font-bold text-center">
                                            {definition.fullName}
                                        </h3>
                                    </div>

                                    {/* Description */}
                                    <p className="text-base leading-relaxed text-gray-700 px-2">
                                        {definition.description}
                                    </p>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default MarketSizeSlide; 