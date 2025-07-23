import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({


    mainTitle: z.string()
        .min(5)
        .max(20)
        .default("TABLE OF CONTENT")
        .meta({
            description: "Main title for the table of contents",
        }),

    subtitle: z.string()
        .min(10)
        .max(25)
        .default("PITCH DECK PRESENTATION")
        .meta({
            description: "Subtitle describing the presentation type",
        }),

    contentItems: z.array(z.object({
        number: z.string().min(1).max(3),
        title: z.string().min(3).max(30)
    })).min(4).max(8).default([
        { number: "01", title: "Hello Friends!" },
        { number: "02", title: "About Us" },
        { number: "03", title: "What We Believe" },
        { number: "04", title: "Problems & Solutions" },
        { number: "05", title: "Market Size" },
        { number: "06", title: "Statistic" }
    ]).meta({
        description: "List of content items with numbers and titles",
    }),

    heroImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1498050108023-c5249f4df085?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Modern laptop and office workspace on wooden desk"
    }).meta({
        description: "Hero image showing professional workspace",
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

    decorativeCircle: z.boolean()
        .default(true)
        .meta({
            description: "Show decorative circle element",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const TableOfContentsSlide = ({ data }: { data: Partial<SchemaType> }) => {


    const { mainTitle, subtitle, contentItems, heroImage, companyLogo, companyName, decorativeCircle } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Content */}
                <div className="w-3/5 px-16 py-12 flex flex-col justify-center">
                    {/* Title Section */}
                    <div className="mb-12">
                        {mainTitle && (
                            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                {mainTitle}
                            </h1>
                        )}

                        {/* Decorative underline */}
                        <div className="w-32 h-1 bg-yellow-300 mb-6"></div>

                        {subtitle && (
                            <p className="text-base font-medium text-gray-700 tracking-wide">
                                {subtitle}
                            </p>
                        )}
                    </div>

                    {/* Content Items */}
                    {contentItems && contentItems.length > 0 && (
                        <div className="grid grid-cols-2 gap-6">
                            {contentItems.map((item, index) => (
                                <div key={index} className="flex items-center space-x-4">
                                    <div className="w-12 h-12 bg-yellow-200 rounded-full flex items-center justify-center flex-shrink-0">
                                        <span className="text-teal-700 font-bold text-lg">
                                            {item.number}
                                        </span>
                                    </div>
                                    <div className="text-gray-900 font-medium text-lg">
                                        {item.title}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Right Side - Image and Branding */}
                <div className="w-2/5 relative bg-teal-600">
                    {/* Hero Image */}
                    {heroImage?.__image_url__ && (
                        <div className="absolute top-0 left-0 right-0 h-1/2">
                            <img
                                src={heroImage.__image_url__}
                                alt={heroImage.__image_prompt__}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    )}

                    {/* Decorative Circle */}
                    {decorativeCircle && (
                        <div className="absolute bottom-24 left-1/2 transform -translate-x-1/2 w-32 h-32 bg-yellow-200 rounded-full"></div>
                    )}

                    {/* Company Branding */}
                    <div className="absolute bottom-8 left-8 flex items-center space-x-3">
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
                            <span className="text-white font-bold text-xl">
                                {companyName}
                            </span>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default TableOfContentsSlide; 