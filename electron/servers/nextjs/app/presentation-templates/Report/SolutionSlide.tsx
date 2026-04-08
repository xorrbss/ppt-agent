import * as z from "zod";


export const slideLayoutId = "report-solution-slide";
export const slideLayoutName = "Report Solution Slide";
export const slideLayoutDescription =
  "A solution slide with a title at the top and a main content area below. The content area supports two structural modes controlled by the showImage boolean: when true, it places one image panel on the left and two numbered content cards on the right; when false, it removes the image and displays three numbered content cards arranged across the content area. Each card contains a short step label and a descriptive text block.";

const CardSchema = z.object({
  stepNumber: z.string().min(2).max(4).meta({
    description: "Short card step number such as 01, 02, or 03.",
  }),
  description: z.string().min(20).max(50).meta({
    description: "Card body copy displayed inside the feature pill.",
  }),
});

export const Schema = z.object({
  title: z.string().min(3).max(12).default("Solution").meta({
    description: "Slide heading shown in the top-left corner.",
  }),
  showImage: z.boolean().default(true).meta({
    description: "Controls whether the image is shown beside the cards.",
  }),
  featureImage: z.object({
    __image_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg"),
    __image_prompt__: z.string().default("Thinking woman portrait on a neutral background"),
  }).default({
    __image_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
    __image_prompt__: "Thinking woman portrait on a neutral background",
  }).meta({
    description: "Optional image used on the left side of the slide.",
  }),
  cards: z
    .array(CardSchema)
    .min(3)
    .max(3)
    .default([
      {
        stepNumber: "01",
        description: "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
      },
      {
        stepNumber: "02",
        description: "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
      },
      {
        stepNumber: "03",
        description: "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
      },
    ])
    .meta({
      description:
        "Three solution cards. When the image is enabled, only the first two cards are displayed.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

type SolutionSlideProps = {
  data: Partial<SchemaType>;
};

function SolutionCard({
  stepNumber,
  description,
}: {
  stepNumber: string;
  description: string;
}) {
  return (
    <div
      className="flex py-[60px] px-10 w-[312px] flex-col items-center justify-center rounded-[160px] bg-[#4d4ef3]  text-center text-white"
      style={{
        backgroundColor: "var(--graph-1,#4d4ef3)",
        color: "var(--primary-text,#ffffff)",
      }}
    >
      <p className="text-[42px] font-medium tracking-[8.709px]">{stepNumber}</p>
      <p className="mt-[27px] text-[27px] min-h-[200px] ">
        {description}
      </p>
    </div>
  );
}

const SolutionSlide = ({ data }: SolutionSlideProps) => {
  const { title, showImage, featureImage, cards } = data;
  const visibleCards = showImage ? cards?.slice(0, 2) : cards;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#F9F8F8]"
      style={{
        backgroundColor: "var(--background-color,#F9F8F8)",
        fontFamily: "var(--body-font-family,Nunito Sans)",
      }}
    >
      <div className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
        style={{ height: 185, backgroundColor: "var(--primary-color,#157CFF)" }}
      />

      <div className="relative z-10 h-full  py-[58px]">
        {title && (
          <h2
            className="text-[80px] px-[64px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {title}
          </h2>
        )}

        <div className="mt-[70px]">
          {showImage ? (
            <div className="flex items-start gap-[40px]">
              {featureImage?.__image_url__ && (
                <div
                  className="h-[396px] w-[534px] shrink-0 overflow-hidden rounded-r-[90px] bg-[#ece8dd]"
                  style={{ backgroundColor: "var(--card-color,#ece8dd)" }}
                >
                  <img
                    src={featureImage?.__image_url__}
                    alt={featureImage?.__image_prompt__}
                    className="h-full w-full object-cover"
                  />
                </div>
              )}

              <div className="flex gap-[40px]">
                {visibleCards?.map((card, index) => (
                  <SolutionCard
                    key={`${card.stepNumber}-${index}`}
                    stepNumber={card.stepNumber}
                    description={card.description}
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="flex justify-center gap-[44px] pt-[6px]">
              {visibleCards?.map((card, index) => (
                <SolutionCard
                  key={`${card.stepNumber}-${index}`}
                  stepNumber={card.stepNumber}
                  description={card.description}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default SolutionSlide;
