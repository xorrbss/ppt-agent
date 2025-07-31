import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("TABLE OF CONTENTS")
        .meta({
            description: "Main heading for the content overview - can be 'Agenda', 'Overview', 'Contents', or similar",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(60)
        .default("PRESENTATION OVERVIEW AND AGENDA")
        .meta({
            description: "Supporting subtitle that explains what the audience will learn or see",
        }),

    contentItems: z.array(z.object({
        itemNumber: z.string().min(1).max(3),
        contentTitle: z.string().min(3).max(40),
        contentDescription: z.string().min(10).max(100).optional()
    })).min(3).max(8).default([
        {
            itemNumber: "01",
            contentTitle: "Introduction & Welcome",
            contentDescription: "Brief overview and objectives"
        },
        {
            itemNumber: "02",
            contentTitle: "About Our Organization",
            contentDescription: "Background and mission"
        },
        {
            itemNumber: "03",
            contentTitle: "Key Challenges",
            contentDescription: "Current issues and opportunities"
        },
        {
            itemNumber: "04",
            contentTitle: "Our Solutions",
            contentDescription: "Proposed approaches and methods"
        },
        {
            itemNumber: "05",
            contentTitle: "Implementation Plan",
            contentDescription: "Timeline and next steps"
        },
        {
            itemNumber: "06",
            contentTitle: "Questions & Discussion",
            contentDescription: "Interactive engagement"
        }
    ]).meta({
        description: "List of presentation sections with numbered sequence and brief descriptions",
    }),

    brandingVisual: ImageSchema.default({
        __image_url__: "https://via.placeholder.com/200x100/22C55E/FFFFFF?text=BRAND",
        __image_prompt__: "Organization logo or brand visual element"
    }).meta({
        description: "Logo or branding element displayed prominently for visual identity",
    }),

    showDecorations: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display decorative visual elements like underlines and accents",
        }),

    useColumnLayout: z.boolean()
        .default(true)
        .meta({
            description: "Whether to arrange content items in two columns for better space utilization",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const TableOfContentsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, contentItems, brandingVisual, showDecorations, useColumnLayout } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Content */}
                <div className="w-3/5 px-16 py-12 flex flex-col justify-start">
                    {/* Title Section */}
                    <div className="mb-12">
                        {sectionTitle && (
                            <h1 className="text-3xl lg:text-4xl font-black text-teal-700 leading-tight mb-4">
                                {sectionTitle}
                            </h1>
                        )}

                        {/* Decorative underline */}
                        {showDecorations && (
                            <div className="w-32 h-1 bg-yellow-300 mb-6"></div>
                        )}

                        {sectionSubtitle && (
                            <p className="text-base font-medium text-gray-700 tracking-wide">
                                {sectionSubtitle}
                            </p>
                        )}
                    </div>

                    {/* Content Items */}
                    {contentItems && contentItems.length > 0 && (
                        <div className={`grid ${useColumnLayout ? 'grid-cols-2' : 'grid-cols-1'} gap-x-16 gap-y-8`}>
                            {contentItems.map((item, index) => (
                                <div key={index} className="flex items-start space-x-4">
                                    {/* Number Circle */}
                                    <div className="w-10 h-10 bg-yellow-200 rounded-full flex items-center justify-center flex-shrink-0">
                                        <span className="text-teal-700 font-bold text-sm">
                                            {item.itemNumber}
                                        </span>
                                    </div>

                                    {/* Content */}
                                    <div className="flex-1">
                                        <h3 className="text-lg font-bold text-gray-900 mb-1">
                                            {item.contentTitle}
                                        </h3>
                                        {item.contentDescription && (
                                            <p className="text-sm text-gray-600 leading-relaxed">
                                                {item.contentDescription}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Right Side - Branding and Visual Elements */}
                <div className="w-2/5 relative bg-gray-50 flex items-center justify-center">
                    {/* Branding Visual */}
                    {brandingVisual?.__image_url__ && (
                        <div className="text-center">
                            <img
                                src={brandingVisual.__image_url__}
                                alt={brandingVisual.__image_prompt__}
                                className="max-w-64 max-h-32 object-contain mx-auto"
                            />
                        </div>
                    )}

                    {/* Decorative Elements */}
                    {showDecorations && (
                        <>
                            {/* Decorative circles */}
                            <div className="absolute top-8 right-8 w-6 h-6 bg-teal-600 rounded-full opacity-60"></div>
                            <div className="absolute bottom-12 left-8 w-4 h-4 bg-yellow-300 rounded-full"></div>
                            <div className="absolute top-32 left-12 w-3 h-3 bg-teal-400 rounded-full"></div>
                        </>
                    )}
                </div>
            </div>

            {/* Bottom accent strip */}
            <div className="absolute bottom-0 left-0 right-0 h-2 bg-teal-600"></div>
        </div>
    );
};

export default TableOfContentsSlide; 