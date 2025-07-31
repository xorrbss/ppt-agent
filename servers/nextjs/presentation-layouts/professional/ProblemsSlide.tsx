import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

// Schema definition
export const Schema = z.object({

    sectionTitle: z.string()
        .min(3)
        .max(25)
        .default("CHALLENGES")
        .meta({
            description: "Main section heading - can be 'Problems', 'Challenges', 'Issues', or similar",
        }),

    sectionSubtitle: z.string()
        .min(10)
        .max(50)
        .default("KEY CHALLENGES TO ADDRESS")
        .meta({
            description: "Supporting subtitle that frames the problem discussion",
        }),

    challengeItems: z.array(z.object({
        itemNumber: z.string().min(1).max(3),
        challengeTitle: z.string().min(5).max(40),
        challengeDescription: z.string().min(20).max(200)
    })).min(2).max(3).default([
        {
            itemNumber: "01",
            challengeTitle: "Inefficient Processes",
            challengeDescription: "Current workflows and systems lack optimization, leading to wasted resources and reduced productivity across all operational areas."
        },
        {
            itemNumber: "02",
            challengeTitle: "Limited Scalability",
            challengeDescription: "Existing infrastructure and methodologies cannot accommodate growth, creating bottlenecks that hinder expansion and progress."
        },
        {
            itemNumber: "03",
            challengeTitle: "Resource Constraints",
            challengeDescription: "Limited availability of key resources including time, budget, and skilled personnel creates barriers to achieving desired outcomes."
        }
    ]).meta({
        description: "List of key challenges or problems with numbered identification and detailed descriptions",
    }),

    supportingVisual: ImageSchema.default({
        image_url_: "https://images.unsplash.com/photo-1542744173-8e7e53415bb0?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80",
        image_prompt_: "Professional workspace showing analysis and problem-solving activities"
    }).meta({
        description: "Visual that supports the problem discussion - could show analysis, challenges, or work environment",
    }),

    showVisualAccents: z.boolean()
        .default(true)
        .meta({
            description: "Whether to display decorative visual accent elements",
        }),

    showColorBlocks: z.boolean()
        .default(true)
        .meta({
            description: "Whether to show colored accent blocks for visual hierarchy",
        }),
})

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const ProblemsSlide = ({ data }: { data: Partial<SchemaType> }) => {

    const { sectionTitle, sectionSubtitle, challengeItems, supportingVisual, showVisualAccents, showColorBlocks } = data;

    return (
        <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
            {/* Main Content Area */}
            <div className="h-full flex">
                {/* Left Side - Content */}
                <div className="w-3/5 relative bg-white px-16 py-12 flex flex-col justify-start">
                    {/* Title Section */}
                    <div className="mb-12">
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
                    </div>

                    {/* Challenge Items List */}
                    {challengeItems && challengeItems.length > 0 && (
                        <div className="space-y-8">
                            {challengeItems.map((item, index) => (
                                <div key={index} className="flex items-start space-x-6">
                                    {/* Number Circle */}
                                    <div className="w-16 h-16 bg-yellow-200 rounded-full flex items-center justify-center flex-shrink-0">
                                        <span className="text-teal-700 font-bold text-xl">
                                            {item.itemNumber}
                                        </span>
                                    </div>

                                    {/* Content */}
                                    <div className="flex-1">
                                        <h3 className="text-xl font-bold text-gray-900 mb-3">
                                            {item.challengeTitle}
                                        </h3>
                                        <p className="text-base leading-relaxed text-gray-700">
                                            {item.challengeDescription}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Right Side - Image and Decorative Elements */}
                <div className="w-2/5 relative">
                    {/* Decorative Circle */}
                    {showVisualAccents && (
                        <div className="absolute top-12 left-8 w-8 h-8 border-4 border-teal-600 rounded-full z-20"></div>
                    )}

                    {/* Supporting Visual */}
                    {supportingVisual?.image_url_ && (
                        <div className="absolute top-8 right-8 bottom-20 left-4  shadow-lg">
                            <img
                                src={supportingVisual.image_url_}
                                alt={supportingVisual.image_prompt_}
                                className="w-full h-full object-cover rounded-lg"
                            />
                        </div>
                    )}

                    {/* Teal Accent Block - Right Edge */}
                    {showColorBlocks && (
                        <div className="absolute top-0 right-0 bottom-0 w-16 bg-teal-600 z-10"></div>
                    )}
                </div>
            </div>

            {/* Bottom Teal Stripe */}
            <div className="absolute bottom-0 left-0 right-0 h-4 bg-teal-600 z-5"></div>
        </div>
    );
};

export default ProblemsSlide; 