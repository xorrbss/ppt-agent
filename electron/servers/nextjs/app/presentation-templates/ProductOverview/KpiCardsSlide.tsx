import * as z from "zod";


export const slideLayoutId = "product-overview-kpi-cards-slide";
export const slideLayoutName = "Product Overview KPI Cards Slide";
export const slideLayoutDescription =
  "A KPI overview slide with a dark-tinted background image, large title, and a two-row grid of six white KPI cards.";

const KpiSchema = z.object({
  value: z.string().max(5).meta({
    description: "Primary KPI value shown in a card.",
  }),
  body: z.string().max(20).meta({
    description: "Short KPI supporting text.",
  }),
});

export const Schema = z.object({
  title: z.string().min(3).max(10).default("KPIs").meta({
    description: "Main title shown in the top-left corner.",
  }),
  kpiIcon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().min(3).max(30).default("pulse icon"),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "pulse icon",
  }).meta({
    description: "Icon shown in each KPI card badge.",
  }),
  backgroundImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1600&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Business team using laptop in meeting"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1600&q=80",
    __image_prompt__: "Business team using laptop in meeting",
  }).meta({
    description: "Background image behind the KPI cards.",
  }),
  items: z
    .array(KpiSchema)
    .min(3)
    .max(6)
    .default([
      { value: "X 5", body: "Lorem ipsum dolor sit." },
      { value: "X 5", body: "Lorem ipsum dolor sit." },
      { value: "X 5", body: "Lorem ipsum dolor sit." },
      { value: "X 5", body: "Lorem ipsum dolor sit." },
      { value: "X 5", body: "Lorem ipsum dolor sit." },
      { value: "X 5", body: "Lorem ipsum dolor sit." },
    ])
    .meta({
      description: "Six KPI cards displayed in a 3x2 grid.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const KpiCardsSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, kpiIcon, backgroundImage, items } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px]"
      style={{
        backgroundColor: "var(--background-color,#DAE1DE)",
        fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
      }}
    >
      {backgroundImage?.__image_url__ && (
        <img
          src={backgroundImage?.__image_url__}
          alt={backgroundImage?.__image_prompt__}
          className="absolute inset-0 h-full w-full object-cover"
        />
      )}

      <div
        className="absolute inset-0"
        style={{
          backgroundColor: "var(--primary-color,#15342D)",
          opacity: 0.8,
        }}
      />

      <div className="relative z-10 px-[66px] pt-[72px] mb-[33px]">
        <h2
          className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#FEFEFF]"
          style={{ color: "var(--primary-text,#FEFEFF)" }}
        >
          {title}
        </h2>
      </div>

      <div className="relative z-10  grid grid-cols-3 gap-x-[30px] gap-y-[19px] px-[66px]">
        {items?.map((item, index) => (
          <div
            key={index}
            className=" bg-[#FEFEFF] p-[33px]"
            style={{ backgroundColor: "var(--card-color,#FEFEFF)" }}
          >
            <div
              className="flex h-[55px] w-[55px] items-center justify-center rounded-full bg-[#15342D]"
              style={{ backgroundColor: "var(--primary-color,#15342D)" }}
            >
              <img
                src={kpiIcon?.__icon_url__}
                alt={kpiIcon?.__icon_query__}
                className="h-[25px] w-[25px] object-contain"
                style={{ filter: "brightness(0) invert(1)" }}
              />
            </div>
            <p
              className="mt-[18px] text-[42px] font-semibold leading-none"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {item.value}
            </p>
            <p
              className="mt-[18px] text-[28px] font-normal  text-[#15342DCC]"
              style={{ color: "var(--background-text,#15342D)" }}
            >
              {item.body}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};

export default KpiCardsSlide;
