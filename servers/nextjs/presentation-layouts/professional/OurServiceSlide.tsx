import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("OUR SERVICES")
        .meta({
            description: "Main section heading - can be 'Our Services', 'What We Offer', 'Service Portfolio', or similar",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(60)
        .default("COMPREHENSIVE SOLUTIONS TAILORED FOR SUCCESS")
        .meta({
            description: "Supporting subtitle that describes the service approach or value proposition",
        }),

    bulletPoints: z.array(z.string().min(10).max(40)).min(4).max(6)
        .default([
            "Customized solutions for your business",
            "Expert guidance and support",
            "Innovative technology solutions",
            "Comprehensive service portfolio"
        ]).meta({
            description: "Bullet points to describe the services offered",
        }),

    serviceHighlight: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1556761175-b413da4baf72?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Professional service delivery or team working on client solutions"
    }).meta({
        description: "Visual that represents service delivery, expertise, or client collaboration",
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
const OurServiceSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, serviceHighlight, showVisualAccents, showColorBlocks, bulletPoints } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left - Title */}
                <div className="w-1/2 px-16 py-12 bg-gray-100">
                    {sectionTitle && (
                        <h1 className="text-3xl lg:text-4xl font-black text-teal-700 leading-tight mb-4">
                            {sectionTitle}
                        </h1>
                    )}

                    {sectionSubtitle && (
                        <p className="text-base font-semibold text-gray-800 tracking-wide">
                            {sectionSubtitle}
                        </p>
                    )}

                    <div className="mt-16 space-y-4">

                        {bulletPoints && bulletPoints.map((point, index) => (
                            <div key={index} className="text-base font-semibold  text-gray-800 tracking-wide">
                                <div className="flex gap-3 items-center">
                                    <div className="min-w-8 min-h-8 bg-teal-700 rounded-full "></div>
                                    <p className="text-lg font-medium text-gray-800 tracking-wide">{point}</p>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Visual Accents */}
                    {showVisualAccents && (
                        <>
                            {/* Decorative elements */}
                            <div className="absolute bottom-16 left-16 w-8 h-8 bg-yellow-300 rounded-full"></div>
                            <div className="absolute top-20 right-20 w-4 h-4 bg-teal-600 rounded-full"></div>
                        </>
                    )}
                </div>

                {/* Right - Service Highlight */}
                <div className="w-1/2 relative">
                    {/* Service Highlight Image */}
                    {serviceHighlight?.__image_url__ && (
                        <div className="h-full w-full">
                            <img
                                src={serviceHighlight.__image_url__}
                                alt={serviceHighlight.__image_prompt__}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    )}

                    {/* Color overlay if enabled */}
                    {showColorBlocks && (
                        <div className="absolute inset-0 bg-teal-600 bg-opacity-20"></div>
                    )}
                </div>
            </div>

            {/* Bottom accent strip */}
            {showColorBlocks && (
                <div className="absolute bottom-0 left-0 right-0 h-3 bg-teal-600"></div>
            )}
        </div>
    );
};

export default OurServiceSlide;