import * as z from "zod";


export const slideLayoutId = "education-services-split-slide";
export const slideLayoutName = "Education Services Split Slide";
export const slideLayoutDescription =
  "A services layout with left heading, one repeated image column, and two stacked service description blocks on the right.";

const ServiceSchema = z.object({
  serviceImage: z.object({
    __image_url__: z.string(),
    __image_prompt__: z.string(),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Team meeting image reused across two rows",
  }).meta({
    description: "Single image reused in the middle column.",
  }),
  heading: z.string().min(3).max(18).meta({
    description: "Service heading shown in the right column.",
  }),
  tagline: z.string().min(3).max(12).meta({
    description: "Short label under each service heading.",
  }),
  body: z.string().min(40).max(90).meta({
    description: "Service description paragraph.",
  }),
});

export const Schema = z.object({
  title: z.string().min(4).max(12).default("Services").meta({
    description: "Main slide title shown on the left.",
  }),
  sections: z
    .array(ServiceSchema)
    .min(2)
    .max(4)
    .default([
      {
        serviceImage: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "Service 1",
        tagline: "TAGLINE",
        body: "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor.",
      },
      {
        serviceImage: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "Service 2",
        tagline: "TAGLINE",
        body: "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor.",
      },
      {
        serviceImage: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "Service 3",
        tagline: "TAGLINE",
        body: "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor.",
      },
      {
        serviceImage: {
          __image_url__:
            "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
          __image_prompt__: "Team meeting image reused across two rows",
        },
        heading: "Service 4",
        tagline: "TAGLINE",
        body: "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor.",
      },
    ])
    .meta({
      description: "Two stacked service content sections on the right side.",
    }),

});

export type SchemaType = z.infer<typeof Schema>;

const EducationServicesSplitSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, sections } = data;


  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden bg-[#E6E7E8]">
      <div className="grid h-full grid-cols-[365px_1fr]">
        <div className="px-[53px] pt-[53px]">
          <h2 className="font-serif text-[64px] leading-[98%] tracking-[-0.02em] text-[#1a1752]">
            {title}
          </h2>
        </div>



        <div className="  grid  "
          style={{
            gridTemplateRows: `repeat(${sections?.length}, 1fr)`,
          }}
        >
          {sections?.map((section, index) => (
            <div key={`${section.heading}-${index}`} className=" flex items-center"
              style={{
                borderBottom: index !== (sections?.length ?? 1) - 1 ? "5px solid rgba(255, 255, 255, 0.10)" : "none",
              }}
            >
              <div className=" min-w-[316px] max-w-[316px] "
                style={{
                  height: sections?.length === 4 ? '175px' : sections?.length === 3 ? '240px' : '357px'
                }}
              >

                <img
                  src={section.serviceImage.__image_url__}
                  alt={section.serviceImage.__image_prompt__}
                  className="h-full w-full object-cover "
                />
              </div>
              <div
                className={`px-[56px] `}
              >
                <h3 className="text-[24px] font-medium leading-none text-[#34394C]">{section.heading}</h3>
                <p className="mt-[10px] text-[14px] font-medium uppercase leading-none text-[#454962]">
                  {section.tagline}
                </p>
                <p className="mt-[18px]  text-[22px] leading-[1.26] text-[#34394C] tracking-[0.04em]">
                  {section.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default EducationServicesSplitSlide;
