import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(30)
        .default("WELCOME!")
        .meta({
            description: "Main greeting or welcome message - can be 'Hello!', 'Welcome!', 'Greetings!', or similar",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(60)
        .default("WE'RE EXCITED TO SHARE OUR STORY")
        .meta({
            description: "Supporting message that sets the tone and builds connection with the audience",
        }),

    welcomeMessage: z.string()
        .min(30)
        .max(200)
        .default("Thank you for joining us today. We're thrilled to have this opportunity to connect with you and share our journey, insights, and vision for the future.")
        .meta({
            description: "Main welcome or introductory message that engages the audience personally",
        }),

    callToActionText: z.string()
        .min(5)
        .max(25)
        .default("Let's Get Started")
        .meta({
            description: "Action button text that encourages audience engagement or progression",
        }),

    speakerImage: ImageSchema.default({
        __image_url__: "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        __image_prompt__: "Professional presenter or team representative in business setting"
    }).meta({
        description: "Image of the presenter, team representative, or welcoming figure",
    }),

    showDecorations: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display decorative visual elements like underlines and accents",
        }),

    showCallToAction: z.boolean()
        .default(true)
        .meta({
            description: "Whether to show the call-to-action button",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const HelloFriendsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, welcomeMessage, callToActionText, speakerImage, showDecorations, showCallToAction } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Teal Background */}
                <div className="w-1/3 relative bg-teal-600">
                    {/* Speech Bubble */}
                    {/* Decorative Circle */}
                </div>

                {/* Right Side - White Background */}
                <div className="w-2/3 relative bg-white">
                    {/* Content */}
                    <div className="pl-32 pr-16 py-12 h-full flex flex-col justify-center">
                        {/* Title Section */}
                        <div className="mb-8">
                            {sectionTitle && (
                                <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-tight mb-4">
                                    {sectionTitle}
                                </h1>
                            )}

                            {sectionSubtitle && (
                                <p className="text-base font-semibold text-gray-800 tracking-wide mb-6">
                                    {sectionSubtitle}
                                </p>
                            )}

                            {/* Decorative underline */}
                            {showDecorations && (
                                <div className="w-32 h-1 bg-yellow-300 mb-8"></div>
                            )}
                        </div>

                        {/* Welcome Text */}
                        {welcomeMessage && (
                            <div className="mb-8">
                                <p className="text-base leading-relaxed text-gray-700">
                                    {welcomeMessage}
                                </p>
                            </div>
                        )}

                        {/* Thank You Button */}
                        {showCallToAction && (
                            <div>
                                <button className="bg-teal-600 hover:bg-teal-700 text-white font-semibold px-8 py-3 rounded-full transition-colors duration-200">
                                    {callToActionText}
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Overlapping Circular Office Image */}
            {speakerImage?.__image_url__ && (
                <div className="absolute top-1/2 left-72 transform -translate-x-1/2 -translate-y-1/2 ">
                    <div className="w-96 h-96 rounded-full overflow-hidden bg-white p-2 shadow-2xl">
                        <img
                            src={speakerImage.__image_url__}
                            alt={speakerImage.__image_prompt__}
                            className="w-full h-full object-cover rounded-full"
                        />
                    </div>
                </div>
            )}
        </div>
    );
};

export default HelloFriendsSlide; 