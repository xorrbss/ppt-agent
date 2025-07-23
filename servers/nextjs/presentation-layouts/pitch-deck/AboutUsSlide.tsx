import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({


    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("ABOUT US")
        .meta({
            description: "Main title for the about us section",
        }),

    subtitle: z.string()
        .min(10)
        .max(20)
        .default("GET TO KNOW US BETTER")
        .meta({
            description: "Subtitle describing the section",
        }),

    companyDescription: z.string()
        .min(100)
        .max(200)
        .default("At our company, we believe in the transformative power of compelling storytelling, data-driven strategies, and cutting-edge creativity. Our mission is simple: to empower businesses with strategic marketing solutions that not only elevate brand visibility but also drive tangible growth and success.")
        .meta({
            description: "Main company description paragraph",
        }),

    additionalText: z.string()
        .min(50)
        .max(200)
        .default("What sets us apart is not just our expertise but our commitment to understanding the unique DNA of each client.")
        .meta({
            description: "Additional descriptive text",
        }),

    businessImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Business person analyzing charts and graphs with pen in hand"
    }).meta({
        description: "Business analytics image showing data analysis",
    }),

    showDecoCircle: z.boolean()
        .default(true)
        .meta({
            description: "Show decorative circle element",
        }),

    showTealAccent: z.boolean()
        .default(true)
        .meta({
            description: "Show teal accent areas",
        }),

    showBeigSquare: z.boolean()
        .default(true)
        .meta({
            description: "Show beige square decoration",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const AboutUsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, companyDescription, additionalText, businessImage, showDecoCircle, showTealAccent, showBeigSquare } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Content */}
                <div className="w-3/5 relative bg-white px-16 py-12 flex flex-col justify-center">
                    {/* Title Section */}
                    <div className="mb-0">
                        {mainTitle && (
                            <h1 className="text-3xl lg:text-4xl font-black text-teal-700 leading-tight mb-4">
                                {mainTitle}
                            </h1>
                        )}

                        {subtitle && (
                            <p className="text-base font-semibold text-gray-800 tracking-wide mb-6">
                                {subtitle}
                            </p>
                        )}

                        {/* Decorative gray line */}
                        <div className="w-1 h-20 bg-gray-400 mb-8"></div>
                    </div>

                    {/* Description Text */}
                    {companyDescription && (
                        <p className="text-base leading-relaxed text-gray-700 max-w-xl">
                            {companyDescription}
                        </p>

                    )}

                    {/* Additional Text */}
                    {additionalText && (
                        <div>
                            <p className="text-base leading-relaxed text-gray-700 max-w-xl">
                                {additionalText}
                            </p>
                        </div>
                    )}
                </div>

                {/* Right Side - Image and Decorative Elements */}
                <div className="w-2/5 relative">
                    {/* Yellow Square - Top Right */}
                    {showBeigSquare && (
                        <div className="absolute bottom-0 right-0 w-24 h-24 bg-yellow-300 z-20"></div>
                    )}

                    {/* Decorative Circle - On Yellow Square */}
                    {showDecoCircle && (
                        <div className="absolute top-6 right-6 w-6 h-6 border-2 border-teal-600 rounded-full z-30"></div>
                    )}

                    {/* Business Image - Left positioned */}
                    {businessImage?.__image_url__ && (
                        <div className="absolute right-36 top-1/2 -translate-y-1/2 h-[500px] w-[350px] z-20 shadow-lg">
                            <img
                                src={businessImage.__image_url__}
                                alt={businessImage.__image_prompt__}
                                className="w-full h-full object-cover"
                            />
                        </div>
                    )}

                    {/* Teal Accent Areas */}
                    {showTealAccent && (
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