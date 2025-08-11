import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

export const layoutName = "Thank You Slide";
export const layoutId = "thank-you-slide";
export const layoutDescription = "A slide with a thank you message";

// Schema definition
export const Schema = z.object({
  organizationName: z
    .string()
    .min(2)
    .max(30)
    .default("Your Organization")
    .meta({
      description: "Name of the organization, company, or entity presenting",
    }),

  primaryMessage: z.string().min(3).max(25).default("THANK YOU").meta({
    description:
      "Main closing message - can be 'Thank You', 'Questions?', 'Let's Connect', or similar",
  }),

  secondaryMessage: z
    .string()
    .min(5)
    .max(60)
    .default("FOR YOUR TIME AND ATTENTION")
    .meta({
      description:
        "Supporting message that completes the primary message or adds context",
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
        "Contact information for follow-up communication and connection",
    }),

  presentationDate: z
    .string()
    .min(3)
    .max(20)
    .default("Current Month Year")
    .meta({
      description:
        "Date when the presentation was given or document was created",
    }),

  showDecorations: z.boolean().default(true).meta({
    description:
      "Whether to display decorative visual elements like background shapes",
  }),

  showNavigationArrow: z.boolean().default(true).meta({
    description:
      "Whether to show a navigation arrow for interactive presentations",
  }),

  showContactInfo: z.boolean().default(true).meta({
    description: "Whether to display contact information in the footer",
  }),
});

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const ThankYouSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const {
    organizationName,
    primaryMessage,
    secondaryMessage,
    brandLogo,
    contactDetails,
    presentationDate,
    showDecorations,
    showNavigationArrow,
    showContactInfo,
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
      <div className="relative z-15 h-full flex flex-col justify-center px-16">
        <div className="max-w-4xl">
          {/* Main Title */}
          {primaryMessage && (
            <h1 className="text-8xl lg:text-9xl font-black text-teal-700 leading-none tracking-tight mb-4">
              {primaryMessage}
            </h1>
          )}

          {/* Subtitle with Circle Bullet */}
          {secondaryMessage && (
            <div className="flex items-center space-x-4 mb-12">
              <div className="w-4 h-4 bg-teal-600 rounded-full"></div>
              <h2 className="text-2xl font-bold text-gray-800 tracking-wide">
                {secondaryMessage}
              </h2>
            </div>
          )}
        </div>
      </div>

      {/* Footer with Contact Info */}
      {showContactInfo && (
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
                  <div className="font-semibold text-gray-900 mb-1">
                    Address
                  </div>
                  <div>{contactDetails.physicalAddress}</div>
                </div>
              )}

              {/* Website */}
              {contactDetails?.websiteUrl && (
                <div>
                  <div className="font-semibold text-gray-900 mb-1">
                    Website
                  </div>
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
      )}
    </div>
  );
};

export default ThankYouSlide;
