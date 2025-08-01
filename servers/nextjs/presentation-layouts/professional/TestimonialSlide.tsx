import * as z from "zod";

import { ImageSchema, IconSchema } from "../defaultSchemes";

export const layoutName = "Testimonial Slide";
export const layoutId = "testimonial-slide";
export const layoutDescription = "A slide with a testimonial";

// Schema definition
export const Schema = z.object({
  sectionTitle: z.string().min(3).max(30).default("CLIENT TESTIMONIALS").meta({
    description:
      "Main section heading - can be 'Testimonials', 'Client Feedback', 'Reviews', or similar",
  }),

  organizationName: z
    .string()
    .min(2)
    .max(30)
    .default("Your Organization")
    .meta({
      description: "Name of the organization or entity being featured",
    }),

  brandLogo: ImageSchema.default({
    __image_url__: "https://via.placeholder.com/40x40/22C55E/FFFFFF?text=L",
    __image_prompt__:
      "Professional organization logo - clean and modern design",
  }).meta({
    description: "Logo or brand mark representing the organization",
  }),

  testimonialItems: z
    .array(
      z.object({
        clientName: z.string().min(2).max(40),
        clientTitle: z.string().min(5).max(60),
        clientCompany: z.string().min(2).max(40),
        testimonialText: z.string().min(50).max(300),
        rating: z.number().min(1).max(5),
        clientPhoto: ImageSchema,
      })
    )
    .min(2)
    .max(3)
    .default([
      {
        clientName: "Sarah Johnson",
        clientTitle: "Chief Executive Officer",
        clientCompany: "TechCorp Solutions",
        testimonialText:
          "Working with this team has been transformative for our business. Their expertise, dedication, and innovative approach exceeded our expectations and delivered remarkable results.",
        rating: 5,
        clientPhoto: {
          __image_url__:
            "https://images.unsplash.com/photo-1494790108755-2616b612b830?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80",
          __image_prompt__: "Professional businesswoman headshot",
        },
      },
      {
        clientName: "Michael Chen",
        clientTitle: "Director of Operations",
        clientCompany: "Global Innovations Inc",
        testimonialText:
          "The level of professionalism and quality of service provided was outstanding. They understood our needs perfectly and delivered solutions that truly made a difference.",
        rating: 5,
        clientPhoto: {
          __image_url__:
            "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80",
          __image_prompt__: "Professional businessman headshot",
        },
      },
      {
        clientName: "Emily Rodriguez",
        clientTitle: "Marketing Manager",
        clientCompany: "Creative Dynamics",
        testimonialText:
          "Exceptional service and results that spoke for themselves. The team's attention to detail and commitment to excellence made our collaboration highly successful.",
        rating: 5,
        clientPhoto: {
          __image_url__:
            "https://images.unsplash.com/photo-1580489944761-15a19d654956?ixlib=rb-4.0.3&auto=format&fit=crop&w=400&q=80",
          __image_prompt__: "Professional woman headshot",
        },
      },
    ])
    .meta({
      description:
        "List of client testimonials with ratings, photos, and detailed feedback",
    }),

  showRatings: z.boolean().default(true).meta({
    description: "Whether to display star ratings for each testimonial",
  }),

  showClientPhotos: z.boolean().default(true).meta({
    description: "Whether to show client photos alongside testimonials",
  }),
});

// Type inference
type SchemaType = z.infer<typeof Schema>;

// Component definition
const TestimonialSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const {
    sectionTitle,
    organizationName,
    brandLogo,
    testimonialItems,
    showRatings,
    showClientPhotos,
  } = data;

  // Helper function to render stars
  const renderStars = (rating: number) => {
    return Array.from({ length: 5 }, (_, i) => (
      <svg
        key={i}
        className={`w-4 h-4 ${
          i < rating ? "text-yellow-400" : "text-gray-300"
        }`}
        fill="currentColor"
        viewBox="0 0 20 20"
      >
        <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
      </svg>
    ));
  };

  return (
    <div className="aspect-video max-w-[1280px] w-full bg-white relative overflow-hidden">
      {/* Header Section */}
      <div className="h-20 bg-teal-600 px-16 py-4 flex justify-between items-center">
        {/* Title */}
        {sectionTitle && (
          <h1 className="text-2xl font-black text-white">{sectionTitle}</h1>
        )}

        {/* Company Branding */}
        <div className="flex items-center space-x-3">
          {brandLogo?.__image_url__ && (
            <div className="w-8 h-8">
              <img
                src={brandLogo.__image_url__}
                alt={brandLogo.__image_prompt__}
                className="w-full h-full object-contain"
              />
            </div>
          )}
          {organizationName && (
            <span className="text-base font-bold text-white">
              {organizationName}
            </span>
          )}
        </div>
      </div>

      {/* Testimonials Content */}
      <div className="flex-1 px-16 py-12 bg-gray-50">
        {testimonialItems && testimonialItems.length > 0 && (
          <div className="grid grid-cols-3 gap-8 h-full">
            {testimonialItems.slice(0, 3).map((item, index) => {
              // Rotate background colors for visual variety
              const bgColors = ["bg-yellow-100", "bg-teal-100", "bg-gray-100"];
              const bgColor = bgColors[index % bgColors.length];

              return (
                <div
                  key={index}
                  className={`${bgColor} rounded-lg p-6 flex flex-col`}
                >
                  {/* Stars Rating */}
                  {showRatings && (
                    <div className="flex space-x-1 mb-4">
                      {renderStars(item.rating)}
                    </div>
                  )}

                  {/* Testimonial Text */}
                  <p className="text-base leading-relaxed text-gray-800 mb-6 flex-1">
                    "{item.testimonialText}"
                  </p>

                  {/* Client Info */}
                  <div className="flex items-center space-x-4">
                    {/* Client Photo */}
                    {showClientPhotos && item.clientPhoto?.__image_url__ && (
                      <div className="w-12 h-12 rounded-full overflow-hidden flex-shrink-0">
                        <img
                          src={item.clientPhoto.__image_url__}
                          alt={item.clientPhoto.__image_prompt__}
                          className="w-full h-full object-cover"
                        />
                      </div>
                    )}

                    {/* Client Details */}
                    <div className="flex-1">
                      <h4 className="text-lg font-bold text-gray-900">
                        {item.clientName}
                      </h4>
                      <p className="text-sm text-gray-600">
                        {item.clientTitle}
                      </p>
                      <p className="text-sm font-semibold text-teal-600">
                        {item.clientCompany}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default TestimonialSlide;
