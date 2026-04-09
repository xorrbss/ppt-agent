import * as z from "zod";


export const slideLayoutId = "cover-slide";
export const slideLayoutName = "Cover Slide";
export const slideLayoutDescription =
  "A full-bleed cover slide with a single background image, a strong violet overlay, and centered company/instructor/presenter/institute name and title text.";

export const Schema = z.object({
  name: z.string().min(3).max(16).optional().default("COMPANY NAME").meta({
    description: "Company/instructor/presenter/institute name label shown above the main title.",
  }),
  title: z.string().min(6).max(32).default("PowerPoint Template").meta({
    description: "Main centered title of the cover slide.",
  }),
  backgroundImage: z.object({
    __image_url__: z.string().default("https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1920&q=80"),
    __image_prompt__: z.string().default("City business district buildings"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1920&q=80",
    __image_prompt__: "City business district buildings",
  }).meta({
    description: "Single background image used across the cover.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const EducationCoverSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { name, title, backgroundImage } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden"
      style={{
        backgroundColor: "var(--background-color,#2e0a8a)",
        fontFamily: "var(--body-font-family,'Times New Roman')",
      }}
    >
      <img
        src={backgroundImage?.__image_url__}
        alt={backgroundImage?.__image_prompt__}
        className="absolute inset-0 h-full w-full object-cover"
      />

      <div
        className="absolute inset-0"
        style={{
          backgroundColor: "var(--primary-color,#3b0bb6)",
          opacity: 0.85,
        }}
      />


      <div
        className="relative z-10 flex h-full flex-col items-center justify-center text-center"
        style={{ color: "var(--primary-text,#ffffff)" }}
      >
        {name && <p className="text-[22px] font-normal uppercase tracking-[0.64px]">{name}</p>
        }        <h1 className="mt-[12px] px-[53px]  text-[64px] font-medium leading-[98%]">
          {title}
        </h1>
      </div>
    </div>
  );
};

export default EducationCoverSlide;
