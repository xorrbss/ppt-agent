import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(10)
        .default("OUR SERVICE")
        .meta({
            description: "Main title for the service section",
        }),



    services: z.array(z.object({
        title: z.string().min(5).max(40),
        description: z.string().min(30).max(100),
        image: ImageSchema
    })).min(3).max(3).default([
        {
            title: "Strategic Brand Development",
            description: "Our agency specializes in strategic brand development, ensuring that your brand not only resonates with your target audience but also stands out in a crowded market.",
            image: {
                __image_url__: "https://images.unsplash.com/photo-1553877522-43269d4ea984?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                __image_prompt__: "Business team working on brand strategy with documents and tablet"
            }
        },
        {
            title: "Data-Driven Marketing",
            description: "Our data-driven approach ensures that every campaign is backed by insights, maximizing ROI and driving tangible results.",
            image: {
                __image_url__: "https://images.unsplash.com/photo-1551288049-bebda4e38f71?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                __image_prompt__: "Business analytics with charts, graphs and keyboard on desk"
            }
        },
        {
            title: "Creative Content Production",
            description: "Content is king, and our agency excels in producing creative, engaging, and impactful content that resonates with your audience.",
            image: {
                __image_url__: "https://images.unsplash.com/photo-1542744094-3a31f272c490?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
                __image_prompt__: "Creative professional working on laptop with design and content"
            }
        }
    ]).meta({
        description: "Three main services with titles, descriptions and images",
    }),

    companyLogo: ImageSchema.default({
        __image_url__: "https://via.placeholder.com/40x40/FFFFFF/1D9A8A?text=C",
        __image_prompt__: "Clean modern company logo icon in white"
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
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const OurServiceSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, services, companyLogo, companyName } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-gray-100 relative overflow-hidden">
            {/* Header with Title and Company Branding */}
            <div className="h-full flex flex-col">
                {/* Top Section */}
                <div className="flex  ">
                    {/* Left - Title */}
                    <div className="w-2/3 px-16 py-12 bg-gray-100">
                        {mainTitle && (
                            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                {mainTitle}
                            </h1>
                        )}
                    </div>

                    {/* Right - Teal Background with Company Branding */}
                    <div className="w-1/3 bg-teal-600 px-16 py-12 flex items-center justify-end">
                        <div className="flex items-center space-x-3">
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
                                <span className="text-xl font-bold text-white">
                                    {companyName}
                                </span>
                            )}
                        </div>
                    </div>
                </div>

                {/* Bottom Section - Services Grid */}
                <div className="flex-1 px-16 py-8 bg-white">
                    {services && services.length >= 3 && (
                        <div className="grid grid-cols-3 gap-8 h-full">
                            {services.slice(0, 3).map((service, index) => (
                                <div key={index} className="flex flex-col">
                                    {/* Service Image */}
                                    <div className="h-48 w-full mb-6">
                                        <img
                                            src={service.image.__image_url__}
                                            alt={service.image.__image_prompt__}
                                            className="w-full h-full object-cover rounded-lg"
                                        />
                                    </div>

                                    {/* Service Content */}
                                    <div className="flex-1">
                                        <h3 className="text-xl font-bold text-teal-700 mb-4 leading-tight">
                                            {service.title}
                                        </h3>
                                        <p className="text-base leading-relaxed text-gray-700">
                                            {service.description}
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

export default OurServiceSlide;