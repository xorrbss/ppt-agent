import * as z from "zod";


export const slideLayoutId = "product-overview-our-services-slide";
export const slideLayoutName = "Product Overview Our Services Slide";
export const slideLayoutDescription =
  "A services slide with title and intro copy on the left, a large image below, and a two-by-two service card matrix on the right.";

const ServiceSchema = z.object({
  heading: z.string().min(4).max(12).meta({
    description: "Service card heading.",
  }),
  body: z.string().max(30).meta({
    description: "Service card short description.",
  }),
  dark: z.boolean().default(false).meta({
    description: "Whether this service card uses the dark style.",
  }),
});

export const Schema = z.object({
  title: z.string().min(6).max(12).default("Our Services").meta({
    description: "Main heading shown at the top-left.",
  }),
  taglineLabel: z.string().min(3).max(10).default("TAGLINE").meta({
    description: "Small label above left paragraph.",
  }),
  taglineBody: z.string().max(30).default(
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea."
  ).meta({
    description: "Supporting text shown beneath the tagline label.",
  }),
  featureImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Customer support team in office"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Customer support team in office",
  }).meta({
    description: "Main image shown at the lower left side.",
  }),
  services: z
    .array(ServiceSchema)

    .max(4)
    .default([
      { heading: "HEADING 1", body: "Lorem ipsum dolor sit amet, consectetur", dark: false },
      { heading: "HEADING 2", body: "Lorem ipsum dolor sit amet, consectetur", dark: true },
      { heading: "HEADING 3", body: "Lorem ipsum dolor sit amet, consectetur", dark: false },
      { heading: "HEADING 4", body: "Lorem ipsum dolor sit amet, consectetur", dark: false },
    ])
    .meta({
      description: "Four service cards rendered on the right side.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const OurServicesSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, taglineLabel, taglineBody, featureImage, services } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] flex items-end pb-[56px]  justify-between overflow-hidden rounded-[24px]"
      style={{
        backgroundColor: "var(--background-color,#DAE1DE)",
        fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
      }}
    >
      <div className=" pt-[74px]">
        <div className="px-[68px]">

          <h2
            className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {title}
          </h2>

          <div className="mt-[26px] w-[560px]">
            <p
              className="text-[20px] font-semibold tracking-[2.074px] text-white"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {taglineLabel}
            </p>
            <p
              className="mt-[14px] text-[24px] font-normal  text-[#15342DCC]"
              style={{ color: "var(--background-text,#15342DCC)" }}
            >
              {taglineBody}
            </p>
          </div>
        </div>
        <div
          className="mt-[35px] h-[326px] w-[650px] bg-[#15342D]"
          style={{ backgroundColor: "var(--primary-color,#15342D)" }}
        >

          {featureImage?.__image_url__ && (
            <img
              src={featureImage?.__image_url__}
              alt={featureImage?.__image_prompt__}
              className="h-[326px] w-[650px] object-cover"
            />
          )}
        </div>
      </div>



      <div className="grid grid-cols-2 gap-[22px] pr-[76px]">
        {services?.map((service, index) => (
          <div
            key={index}
            className=" p-[33px]"
            style={{
              backgroundColor: service.dark
                ? "var(--primary-color,#15342D)"
                : "var(--card-color,#ececee)",
            }}
          >
            <p
              className="text-[20px] font-semibold tracking-[2.074px] text-white"
              style={{
                color: service.dark
                  ? "var(--primary-text,#edf2f1)"
                  : "var(--primary-color,#15342D)",
              }}
            >
              {service.heading}
            </p>
            <p
              className={`${service.dark ? "text-white" : "text-[#15342DCC]"} mt-[20px] text-[28px] font-normal`}
              style={{
                color: service.dark
                  ? "var(--primary-text,#edf2f1)"
                  : "var(--background-text,#15342DCC)",
              }}
            >
              {service.body}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};

export default OurServicesSlide;
