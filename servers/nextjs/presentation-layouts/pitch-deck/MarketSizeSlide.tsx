import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("MARKET ANALYSIS")
        .meta({
            description: "Main section heading - can be 'Market Size', 'Market Opportunity', 'Industry Overview', or similar",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(60)
        .default("UNDERSTANDING THE OPPORTUNITY LANDSCAPE")
        .meta({
            description: "Supporting subtitle that frames the market discussion and opportunity scope",
        }),

    marketDefinitions: z.array(z.object({
        marketType: z.string().min(3).max(30),
        marketDescription: z.string().min(20).max(150),
        marketValue: z.string().min(3).max(25).optional()
    })).min(2).max(4).default([
        {
            marketType: "Total Addressable Market (TAM)",
            marketDescription: "The overall revenue opportunity available if we achieved 100% market share across all segments and geographies.",
            marketValue: "$50B"
        },
        {
            marketType: "Serviceable Addressable Market (SAM)",
            marketDescription: "The portion of TAM targeted by our products and services within our geographic reach.",
            marketValue: "$15B"
        },
        {
            marketType: "Serviceable Obtainable Market (SOM)",
            marketDescription: "The portion of SAM that we can realistically capture based on our resources and market conditions.",
            marketValue: "$3B"
        }
    ]).meta({
        description: "List of market definitions and opportunities with descriptions and potential values",
    }),

    visualRepresentation: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1597149962419-0d900ac2b46c?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "World map showing global market reach and geographic distribution"
    }).meta({
        description: "Visual that represents market scope - could be a world map, chart, or geographic visualization",
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

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const MarketSizeSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, marketDefinitions, visualRepresentation, showYellowUnderline, showVisualAccents } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Content */}
                <div className="w-3/5 relative bg-white px-16 py-12 flex flex-col justify-start">
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

                    {/* Market Definitions List */}
                    {marketDefinitions && marketDefinitions.length > 0 && (
                        <div className="space-y-6">
                            {marketDefinitions.map((market, index) => (
                                <div key={index} className="border-l-4 border-teal-600 pl-6">
                                    <div className="flex items-center justify-between mb-2">
                                        <h3 className="text-lg font-bold text-gray-900">
                                            {market.marketType}
                                        </h3>
                                        {market.marketValue && (
                                            <span className="text-xl font-bold text-teal-600">
                                                {market.marketValue}
                                            </span>
                                        )}
                                    </div>
                                    <p className="text-base leading-relaxed text-gray-700">
                                        {market.marketDescription}
                                    </p>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Right Side - Visual Representation */}
                <div className="w-2/5 relative bg-gray-50">
                    {/* Visual Accents */}
                    {showVisualAccents && (
                        <>
                            {/* Decorative circles */}
                            <div className="absolute top-8 right-8 w-6 h-6 bg-teal-600 rounded-full opacity-60 z-20"></div>
                            <div className="absolute bottom-12 left-8 w-4 h-4 bg-yellow-300 rounded-full z-20"></div>
                        </>
                    )}

                    {/* Visual Representation */}
                    {visualRepresentation?.__image_url__ && (
                        <div className="absolute inset-8 z-15 shadow-lg">
                            <img
                                src={visualRepresentation.__image_url__}
                                alt={visualRepresentation.__image_prompt__}
                                className="w-full h-full object-cover rounded-lg"
                            />
                        </div>
                    )}
                </div>
            </div>

            {/* Bottom accent strip */}
            <div className="absolute bottom-0 left-0 right-0 h-3 bg-teal-600"></div>
        </div>
    );
};

export default MarketSizeSlide; 