import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({


    companyName: z.string()
        .min(2)
        .max(15)
        .default("Company Name")
        .meta({
            description: "Company name displayed prominently",
        }),

    mainTitle: z.string()
        .min(5)
        .max(20)
        .default("THANK YOU")
        .meta({
            description: "Main thank you title in large bold letters",
        }),

    subtitle: z.string()
        .min(10)
        .max(40)
        .default("FOR YOUR NICE ATTENTION")
        .meta({
            description: "Subtitle thanking the audience",
        }),

    companyLogo: ImageSchema.default({
        __image_url__: "https://via.placeholder.com/40x40/22C55E/FFFFFF?text=C",
        __image_prompt__: "Company logo - geometric green icon"
    }).meta({
        description: "Company logo icon",
    }),

    contactInfo: z.object({
        telephone: z.string().min(10).max(20).default("+123-456-7890"),
        address: z.string().min(10).max(50).default("123 Anywhere St., Any City, ST 12345"),
        website: z.string().min(10).max(30).default("www.reallygreatsite.com")
    }).default({
        telephone: "+123-456-7890",
        address: "123 Anywhere St., Any City, ST 12345",
        website: "www.reallygreatsite.com"
    }).meta({
        description: "Company contact information",
    }),

    presentationDate: z.string()
        .min(5)
        .max(20)
        .default("December 2023")
        .meta({
            description: "Date of the presentation",
        }),

    decorativeCircle: z.boolean()
        .default(true)
        .meta({
            description: "Show decorative circle element",
        }),

    arrowButton: z.boolean()
        .default(true)
        .meta({
            description: "Show navigation arrow button",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const ThankYouSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { companyName, mainTitle, subtitle, companyLogo, contactInfo, presentationDate, decorativeCircle, arrowButton } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Header with Logo and Arrow */}
            <div className="absolute top-0 left-0 right-0 px-16 py-8 flex justify-between items-center z-20">
                {/* Company Logo and Name */}
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
                        <span className="text-2xl font-bold text-gray-900">
                            {companyName}
                        </span>
                    )}
                </div>

                {/* Arrow Button */}
                {arrowButton && (
                    <div className="w-12 h-12 bg-teal-600 rounded-full flex items-center justify-center">
                        <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                        </svg>
                    </div>
                )}
            </div>

            {/* Decorative Circle */}
            {decorativeCircle && (
                <div className="absolute top-20 right-16 w-96 h-96 bg-yellow-100 rounded-full opacity-60 z-10"></div>
            )}

            {/* Main Content */}
            <div className="relative z-15 h-full flex flex-col justify-center px-16">
                <div className="max-w-4xl">
                    {/* Main Title */}
                    {mainTitle && (
                        <h1 className="text-8xl lg:text-9xl font-black text-teal-700 leading-none tracking-tight mb-4">
                            {mainTitle}
                        </h1>
                    )}

                    {/* Subtitle with Circle Bullet */}
                    {subtitle && (
                        <div className="flex items-center space-x-4 mb-12">
                            <div className="w-4 h-4 bg-teal-600 rounded-full"></div>
                            <h2 className="text-2xl font-bold text-gray-800 tracking-wide">
                                {subtitle}
                            </h2>
                        </div>
                    )}
                </div>
            </div>

            {/* Footer with Contact Info */}
            <div className="absolute bottom-0 left-0 right-0 px-16 py-8 border-t-2 border-gray-300">
                <div className="flex justify-between items-center text-gray-700">
                    <div className="flex space-x-16 text-sm">
                        {/* Telephone */}
                        {contactInfo?.telephone && (
                            <div>
                                <div className="font-semibold text-gray-900 mb-1">Telephone</div>
                                <div>{contactInfo.telephone}</div>
                            </div>
                        )}

                        {/* Address */}
                        {contactInfo?.address && (
                            <div>
                                <div className="font-semibold text-gray-900 mb-1">Address</div>
                                <div>{contactInfo.address}</div>
                            </div>
                        )}

                        {/* Website */}
                        {contactInfo?.website && (
                            <div>
                                <div className="font-semibold text-gray-900 mb-1">Website</div>
                                <div>{contactInfo.website}</div>
                            </div>
                        )}
                    </div>

                    {/* Presentation Date */}
                    {presentationDate && (
                        <div className="text-right">
                            <div className="text-lg font-bold text-gray-900">
                                {presentationDate}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default ThankYouSlide; 