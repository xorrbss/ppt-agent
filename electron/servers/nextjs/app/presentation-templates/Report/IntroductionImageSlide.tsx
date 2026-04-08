import * as z from "zod";


export const slideLayoutId = "introduction-image-slide";
export const slideLayoutName = "Introduction Image Slide";
export const slideLayoutDescription =
  "A slide with a title at the top-left, a paragraph block beneath the title, a short bulleted list in the lower-left area, and a large supporting image anchored on the right side of the slide.";

export const Schema = z.object({
  title: z.string().min(3).max(12).default("Introduction").meta({
    description: "Slide title shown at the top-left.",
  }),
  body: z.string().max(250).default(
    "Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut alut enim ad minima veniam, quis. Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut alut enim ad minima veniam, quis"
  ).meta({
    description: "Primary paragraph shown under the title.",
  }),
  bullets: z
    .array(z.string().max(35))

    .max(4)
    .default([
      "Ut enim ad minima veniam, quis nostrum",
      "Exercitationem ullam corporis suscipit",
      "Ut enim ad minima veniam, quis nostrum",
      "exercitationem ullam corporis suscipit",
    ])
    .meta({
      description: "Bullet list shown in the lower-left area.",
    }),
  featureImage: z.object({
    __image_url__: z.string(),
    __image_prompt__: z.string(),
  }).optional().meta({
    description: "Large image shown on the right side of the slide or optional.",
  }).default({
    __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
    __image_prompt__: "Thoughtful woman portrait on a neutral backdrop",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const IntroductionImageSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, body, bullets, featureImage } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#f9f8f8]"
      style={{
        backgroundColor: "var(--background-color,#f9f8f8)",
        fontFamily: "var(--body-font-family,Nunito Sans)",
      }}
    >
      <div
        className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
        style={{ height: 185, backgroundColor: "var(--primary-color,#157CFF)" }}
      />

      <div className="px-[74px] pt-[76px]">
        <h2
          className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
          style={{ color: "var(--background-text,#232223)" }}
        >
          {title}
        </h2>
      </div>

      <div className="flex gap-28 pl-[96px] pt-[30px]">
        <div className="flex  flex-col">
          <p className=" text-[24px] leading-[26.667px] text-[#232223]" style={{ color: "var(--background-text,#232223)" }}>
            {body}
          </p>

          <ul
            className="mt-8 list-disc pl-[28px] text-[24px] leading-[26.667px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {bullets?.map((bullet, index) => (
              <li key={`${bullet}-${index}`} className="mt-[8px]">
                {bullet}
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-1 items-end justify-end">
          <div
            className="h-[397px] w-[582px] overflow-hidden rounded-l-[106px] bg-[#157CFF]"
            style={{ backgroundColor: "var(--primary-color,#157CFF)" }}
          >
            <img
              src={featureImage?.__image_url__}
              alt={featureImage?.__image_prompt__}
              className="h-full w-full object-cover"
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default IntroductionImageSlide;
