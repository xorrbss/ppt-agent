import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

export const layoutName = "Title Slide";
export const layoutId = "title-slide";
export const layoutDescription = "A slide with a title and subtitle";

// Schema definition
export const Schema = z.object({
  organizationName: z
    .string()
    .min(2)
    .max(25)
    .default("Your Organization")
    .meta({
      description: "Name of the organization, company, or entity presenting",
    }),

  primaryTitle: z.string().min(3).max(30).default("PRESENTATION TITLE").meta({
    description:
      "Main headline or title for the presentation - should be impactful and attention-grabbing",
  }),

  secondaryTitle: z
    .string()
    .min(5)
    .max(50)
    .default("PROFESSIONAL PRESENTATION")
    .meta({
      description:
        "Subtitle that provides context about the presentation type or purpose",
    }),

  brandLogo: ImageSchema.default({
    __image_url__: "https://via.placeholder.com/40x40/22C55E/FFFFFF?text=L",
    __image_prompt__:
      "Professional organization logo - clean and modern design",
  }).meta({
    description: "Logo or brand mark representing the presenting organization",
  }),

  contactDetails: z
    .object({
      phoneNumber: z.string().min(10).max(20).default("+1-234-567-8900"),
      physicalAddress: z
        .string()
        .min(10)
        .max(60)
        .default("123 Business Ave, City, State 12345"),
      websiteUrl: z
        .string()
        .min(10)
        .max(40)
        .default("www.yourorganization.com"),
    })
    .default({
      phoneNumber: "+1-234-567-8900",
      physicalAddress: "123 Business Ave, City, State 12345",
      websiteUrl: "www.yourorganization.com",
    })
    .meta({
      description:
        "Contact information including phone, address, and website for follow-up communication",
    }),

  presentationDate: z
    .string()
    .min(3)
    .max(20)
    .default("Current Month Year")
    .meta({
      description: "Date when the presentation is being given or was created",
    }),

  showDecorations: z.boolean().default(true).meta({
    description:
      "Whether to display decorative visual elements like background shapes",
  }),

  showNavigationArrow: z.boolean().default(true).meta({
    description:
      "Whether to show a navigation arrow button for presentation flow",
  }),
});

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const ThynkTitleSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const {
    organizationName,
    primaryTitle,
    secondaryTitle,
    brandLogo,
    contactDetails,
    presentationDate,
    showDecorations,
    showNavigationArrow,
  } = data;

  return (
    <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
      {/* Header with Logo and Arrow */}
      <div className="absolute top-0 left-0 right-0 px-16 py-8 flex justify-between items-center z-20">
        {/* Company Logo and Name */}
        <div className="flex items-center space-x-3">
          {brandLogo?.__image_url__ && (
            <div className="w-10 h-10">
              <img
                src={brandLogo.__image_url__}
                alt={brandLogo.__image_prompt__}
                className="w-full h-full object-contain"
              />
            </div>
          )}
          {organizationName && (
            <span className="text-2xl font-bold text-gray-900">
              {organizationName}
            </span>
          )}
        </div>

        {/* Arrow Button */}
        {showNavigationArrow && (
          <div className="w-12 h-12 bg-teal-600 rounded-full flex items-center justify-center">
            <svg
              className="w-6 h-6 text-white"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 5l7 7-7 7"
              />
            </svg>
          </div>
        )}
      </div>

      {/* Decorative Circle */}
      {showDecorations && (
        <div className="absolute top-20 right-16 w-96 h-96 bg-yellow-100 rounded-full opacity-60 z-10"></div>
      )}

      {/* Main Content */}
      <div className="relative  h-full flex flex-col justify-center px-16">
        <div className="">
          {/* Main Title */}
          {primaryTitle && (
            <h1 className="text-4xl lg:text-5xl font-black text-teal-700 leading-none tracking-tight mb-4">
              {primaryTitle}
            </h1>
          )}

          {/* Subtitle with Circle Bullet */}
          {secondaryTitle && (
            <div className="flex items-center space-x-4 mb-12">
              <div className="w-4 h-4 bg-teal-600 rounded-full"></div>
              <h2 className="text-xl font-bold text-gray-800 tracking-wide">
                {secondaryTitle}
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
            {contactDetails?.phoneNumber && (
              <div>
                <div className="font-semibold text-gray-900 mb-1">
                  Telephone
                </div>
                <div>{contactDetails.phoneNumber}</div>
              </div>
            )}

            {/* Address */}
            {contactDetails?.physicalAddress && (
              <div>
                <div className="font-semibold text-gray-900 mb-1">Address</div>
                <div>{contactDetails.physicalAddress}</div>
              </div>
            )}

            {/* Website */}
            {contactDetails?.websiteUrl && (
              <div>
                <div className="font-semibold text-gray-900 mb-1">Website</div>
                <div>{contactDetails.websiteUrl}</div>
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

export default ThynkTitleSlide;
