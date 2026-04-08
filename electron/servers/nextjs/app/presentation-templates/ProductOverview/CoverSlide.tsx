import * as z from "zod";



export const slideLayoutId = "product-overview-cover-slide";
export const slideLayoutName = "Product Overview Cover Slide";
export const slideLayoutDescription =
  "A cover slide with a compact logo in the top-left, a date in the top-right, a two-line centered title, and a city/building image anchored to the bottom with a soft fade into the background.";

export const Schema = z.object({
  logoImage: z.object({
    __image_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg"),

    __image_prompt__: z.string().min(10).max(100).default("Professional logo of the company"),
  }).default({
    __image_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
    __image_prompt__: "Professional logo of the company",
  }),
  label: z.string().min(3).max(16).default("MARCH 2026").meta({
    description: "Date label shown at the top-right corner.",
  }),
  titleLine1: z.string().min(3).max(18).default("Social Media").meta({
    description: "First line of the cover title.",
  }),
  titleLine2: z.string().min(3).max(20).default("Marketing Report").meta({
    description: "Second line of the cover title.",
  }),
  backgroundImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1920&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Tall glass buildings from street view"),
  }).default({
    __image_url__: "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1920&q=80",
    __image_prompt__: "Tall glass buildings from street view",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CoverSlide = ({ data }: { data: Partial<SchemaType> }) => {

  const { logoImage, label, titleLine1, titleLine2, backgroundImage } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px]"
      style={{
        backgroundColor: "var(--background-color,#DAE1DE)",
        fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
      }}
    >
      <div
        className="relative z-20 flex h-full flex-col px-[36px] pt-[62px] text-[#15342D]"
        style={{ color: "var(--primary-color,#15342D)" }}
      >
        <div className="flex items-center justify-between">

          <img
            src={logoImage?.__image_url__ ?? ''}
            alt={logoImage?.__image_prompt__ || ''}
            className="h-[42px] w-[171px] object-cover"
          />

          <p
            className="text-[18px] font-normal leading-[18.991px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {label}
          </p>
        </div>

        <div className="flex flex-1 items-center justify-center pb-[80px]">
          <h1
            className="text-center text-[100px] font-semibold leading-[108.4%] tracking-[-3.024px]"
          >
            {titleLine1}
            <br />
            {titleLine2}
          </h1>
        </div>
      </div>

      {backgroundImage?.__image_url__ && (
        <img
          src={backgroundImage.__image_url__ || ''}
          alt={backgroundImage.__image_prompt__ || ''}
          className="absolute bottom-0 left-0 z-0 h-[360px] w-full object-cover"
        />
      )}

      <div
        className="pointer-events-none absolute bottom-0 left-0 w-full z-10"
        style={{
          height: "365px",
          background:
            "linear-gradient(0deg, rgba(218, 225, 222, 0.00) 0%, var(--background-color,#DAE1DE) 80.33%)",
        }}
      />
    </div>
  );
};

export default CoverSlide;
