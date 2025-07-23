import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    mainTitle: z.string()
        .min(5)
        .max(15)
        .default("HELLO FRIENDS!")
        .meta({
            description: "Main greeting title",
        }),

    subtitle: z.string()
        .min(5)
        .max(20)
        .default("GREETING FROM US")
        .meta({
            description: "Subtitle for the greeting",
        }),

    welcomeText: z.string()
        .min(50)
        .max(300)
        .default("Ladies and gentlemen, a warm welcome to our business pitch deck presentation. Your time and attention are greatly appreciated. Today, we're excited to share our vision, accomplishments, and the exciting roadmap ahead. Let's embark on this journey together, and thank you for considering an investment in our innovative venture.")
        .meta({
            description: "Main welcome message text",
        }),

    officeImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1497366216548-37526070297c?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Modern office workspace with desk, computer, and large window view"
    }).meta({
        description: "Office workspace image in circular frame",
    }),

    buttonText: z.string()
        .min(3)
        .max(20)
        .default("Thank you")
        .meta({
            description: "Text for the call-to-action button",
        }),

    showSpeechBubble: z.boolean()
        .default(true)
        .meta({
            description: "Show decorative speech bubble",
        }),

    showDecoCircle: z.boolean()
        .default(true)
        .meta({
            description: "Show decorative circle element",
        }),

})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const HelloFriendsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { mainTitle, subtitle, welcomeText, officeImage, buttonText, showSpeechBubble, showDecoCircle } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Teal Background */}
                <div className="w-1/3 relative bg-teal-600">
                    {/* Speech Bubble */}
                    {showSpeechBubble && (
                        <div className="absolute top-16 left-16 z-20">
                            <div className="w-16 h-10 bg-yellow-200 rounded-2xl relative">
                                <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 flex space-x-1">
                                    <div className="w-2 h-2 bg-teal-600 rounded-full"></div>
                                    <div className="w-2 h-2 bg-teal-600 rounded-full"></div>
                                    <div className="w-2 h-2 bg-teal-600 rounded-full"></div>
                                </div>
                                {/* Speech bubble tail */}
                                <div className="absolute bottom-0 left-6 transform translate-y-full">
                                    <div className="w-0 h-0 border-l-4 border-r-4 border-t-8 border-l-transparent border-r-transparent border-t-yellow-200"></div>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Decorative Circle */}
                    {showDecoCircle && (
                        <div className="absolute bottom-16 left-16 w-8 h-8 border-4 border-white rounded-full z-20"></div>
                    )}
                </div>

                {/* Right Side - White Background */}
                <div className="w-2/3 relative bg-white">
                    {/* Content */}
                    <div className="pl-32 pr-16 py-12 h-full flex flex-col justify-center">
                        {/* Title Section */}
                        <div className="mb-8">
                            {mainTitle && (
                                <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                    {mainTitle}
                                </h1>
                            )}

                            {subtitle && (
                                <p className="text-base font-semibold text-gray-800 tracking-wide mb-6">
                                    {subtitle}
                                </p>
                            )}

                            {/* Decorative underline */}
                            <div className="w-32 h-1 bg-yellow-300 mb-8"></div>
                        </div>

                        {/* Welcome Text */}
                        {welcomeText && (
                            <div className="mb-8">
                                <p className="text-base leading-relaxed text-gray-700">
                                    {welcomeText}
                                </p>
                            </div>
                        )}

                        {/* Thank You Button */}
                        {buttonText && (
                            <div>
                                <button className="bg-teal-600 hover:bg-teal-700 text-white font-semibold px-8 py-3 rounded-full transition-colors duration-200">
                                    {buttonText}
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Overlapping Circular Office Image */}
            {officeImage?.__image_url__ && (
                <div className="absolute top-1/2 left-72 transform -translate-x-1/2 -translate-y-1/2 z-30">
                    <div className="w-96 h-96 rounded-full overflow-hidden bg-white p-2 shadow-2xl">
                        <img
                            src={officeImage.__image_url__}
                            alt={officeImage.__image_prompt__}
                            className="w-full h-full object-cover rounded-full"
                        />
                    </div>
                </div>
            )}
        </div>
    );
};

export default HelloFriendsSlide; 