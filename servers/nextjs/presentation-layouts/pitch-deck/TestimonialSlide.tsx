import * as z from "zod";
import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("TESTIMONIAL")
        .meta({
            description: "Main title for the testimonial section",
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

    testimonials: z.array(z.object({
        text: z.string().min(30).max(200),
        clientName: z.string().min(2).max(30),
        clientPhoto: ImageSchema,
        rating: z.number().min(1).max(5),
        backgroundColor: z.enum(["beige", "teal", "light"])
    })).min(3).max(3).default([
        {
            text: "In a world flooded with marketing noise, this company stands out as a beacon of creativity and effectiveness.",
            clientName: "Benjamin Shah",
            clientPhoto: {
                __image_url__: "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80",
                __image_prompt__: "Professional headshot of smiling businessman"
            },
            rating: 5,
            backgroundColor: "beige"
        },
        {
            text: "The level of expertise and personalized attention to our unique needs has made them an invaluable partner.",
            clientName: "Murad Naser",
            clientPhoto: {
                __image_url__: "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80",
                __image_prompt__: "Professional headshot of confident businessman with beard"
            },
            rating: 5,
            backgroundColor: "teal"
        },
        {
            text: "I've been thoroughly impressed with the exceptional level of service and creativity they bring to the table.",
            clientName: "Drew Feig",
            clientPhoto: {
                __image_url__: "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80",
                __image_prompt__: "Professional headshot of smiling young businessman"
            },
            rating: 5,
            backgroundColor: "light"
        }
    ]).meta({
        description: "Three client testimonials with photos and ratings",
    }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const TestimonialSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, companyLogo, companyName, testimonials } = data;

    const getBackgroundClass = (bg: string) => {
        switch (bg) {
            case "teal": return "bg-teal-600 text-white";
            case "beige": return "bg-yellow-100 text-gray-900";
            case "light": return "bg-gray-100 text-gray-900";
            default: return "bg-gray-100 text-gray-900";
        }
    };

    const getStarColor = (bg: string) => {
        return bg === "teal" ? "text-yellow-400" : "text-yellow-500";
    };

    const renderStars = (rating: number, backgroundColor: string) => {
        return (
            <div className={`flex space-x-1 mb-6 ${getStarColor(backgroundColor)}`}>
                {[...Array(5)].map((_, index) => (
                    <svg
                        key={index}
                        className="w-6 h-6 fill-current"
                        viewBox="0 0 24 24"
                    >
                        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                    </svg>
                ))}
            </div>
        );
    };

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-gray-50 relative overflow-hidden">
            {/* Header Section */}
            <div className="h-20 bg-teal-600 px-16 py-4 flex justify-between items-center">
                {/* Title */}
                {mainTitle && (
                    <h1 className="text-4xl font-black text-white">
                        {mainTitle}
                    </h1>
                )}

                {/* Company Branding */}
                <div className="flex items-center space-x-3">
                    {companyLogo?.__image_url__ && (
                        <div className="w-8 h-8">
                            <img
                                src={companyLogo.__image_url__}
                                alt={companyLogo.__image_prompt__}
                                className="w-full h-full object-contain"
                            />
                        </div>
                    )}
                    {companyName && (
                        <span className="text-lg font-bold text-white">
                            {companyName}
                        </span>
                    )}
                </div>
            </div>

            {/* Testimonials Section */}
            <div className="flex-1 h-[calc(100%-80px)] px-16 py-12 flex items-center justify-center">
                {testimonials && testimonials.length > 0 && (
                    <div className="grid grid-cols-3 gap-8 w-full max-w-6xl">
                        {testimonials.map((testimonial, index) => (
                            <div
                                key={index}
                                className={`${getBackgroundClass(testimonial.backgroundColor)} rounded-3xl p-8 flex flex-col items-center text-center h-full`}
                            >
                                {/* Stars */}
                                {renderStars(testimonial.rating, testimonial.backgroundColor)}

                                {/* Testimonial Text */}
                                <p className="text-base leading-relaxed mb-8 flex-1 flex items-center">
                                    {testimonial.text}
                                </p>

                                {/* Client Photo */}
                                {testimonial.clientPhoto?.__image_url__ && (
                                    <div className="w-20 h-20 rounded-full overflow-hidden mb-4 border-4 border-white shadow-lg">
                                        <img
                                            src={testimonial.clientPhoto.__image_url__}
                                            alt={testimonial.clientPhoto.__image_prompt__}
                                            className="w-full h-full object-cover"
                                        />
                                    </div>
                                )}

                                {/* Client Name */}
                                <h3 className="text-lg font-bold">
                                    {testimonial.clientName}
                                </h3>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default TestimonialSlide; 