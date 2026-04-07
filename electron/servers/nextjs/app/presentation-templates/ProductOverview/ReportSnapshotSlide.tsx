import * as z from "zod";


export const slideLayoutId = "product-overview-report-snapshot-slide";
export const slideLayoutName = "Product Overview Report Snapshot Slide";
export const slideLayoutDescription =
  "A report summary slide with a left-edge photo strip, title and intro copy, a compact bar chart card, and a KPI callout card on the right.";

const BarSchema = z.object({
  value: z.number().min(10).max(100).meta({
    description: "Relative bar value used in the spending mini chart.",
  }),
});

export const Schema = z.object({
  title: z.string().min(4).max(12).default("Report").meta({
    description: "Slide heading text.",
  }),
  taglineLabel: z.string().min(3).max(10).default("TAGLINE").meta({
    description: "Small label above intro paragraph.",
  }),
  taglineBody: z.string().min(40).max(100).default(
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea."
  ).meta({
    description: "Intro paragraph shown beneath the heading.",
  }),
  sideImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1520607162513-77705c0f0d4a?auto=format&fit=crop&w=700&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Team members reviewing charts together"),
  }).default({
    __image_url__:
      "https://images.unsplash.com/photo-1520607162513-77705c0f0d4a?auto=format&fit=crop&w=700&q=80",
    __image_prompt__: "Team members reviewing charts together",
  }).meta({
    description: "Left-side vertical image strip.",
  }),
  chartTitle: z.string().min(3).max(20).default("Sandro Tavares").meta({
    description: "Name displayed in the chart card.",
  }),
  bars: z
    .array(BarSchema)
    .min(8)
    .max(8)
    .default([
      { value: 52 },
      { value: 24 },
      { value: 35 },
      { value: 48 },
      { value: 26 },
      { value: 72 },
      { value: 47 },
      { value: 55 },
    ])
    .meta({
      description: "Eight bars used in the spending card chart.",
    }),
  metricValue: z.string().min(1).max(8).default("X 5").meta({
    description: "KPI value in the callout card.",
  }),
  metricBody: z.string().min(10).max(18).default("Lorem ipsum.").meta({
    description: "KPI short text in the callout card.",
  }),
  metricIcon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().min(3).max(30).default("pulse icon"),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "pulse icon",
  }).meta({
    description: "Icon shown in the KPI callout card.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const ReportSnapshotSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const {
    title,
    taglineLabel,
    taglineBody,
    sideImage,
    chartTitle,
    bars,
    metricValue,
    metricBody,
    metricIcon,
  } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] flex gap-[44px]"
      style={{ backgroundColor: "#DAE1DE" }}
    >
      {sideImage?.__image_url__ && (
        <img
          src={sideImage?.__image_url__}
          alt={sideImage?.__image_prompt__}
          className=" h-full w-[232px] object-cover"
        />
      )}

      <div className=" pt-[74px]">
        <h2
          className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
          style={{ color: "#15342D" }}
        >
          {title}
        </h2>

        <div className="mt-[17px] w-[560px]">
          <p
            className="text-[20px] font-semibold tracking-[2.074px] text-[#15342D]"
            style={{ color: "#15342D" }}
          >
            {taglineLabel}
          </p>
          <p className="mt-[13px] text-[24px] font-normal  text-[#15342DCC]">{taglineBody}</p>
        </div>
      </div>

      <div className="absolute bottom-[56px] left-[268px]  w-[574px] bg-[#ececee] px-[24px] py-[20px]">
        <p className="text-[20px] text-[#6a6a6a]">Spendings</p>
        <p className="mt-[14px] text-[28px] font-normal  text-[#15342DCC]" style={{ color: "#15342D" }}>
          {chartTitle}
        </p>

        <div className="mt-[24px] flex h-[124px] items-end gap-[22px] border-b border-[#d8dcdb] pb-[10px]">
          {bars?.map((bar, index) => (
            <div key={index} className="w-[24px] rounded-[4px] bg-[#0b4b40]" style={{ height: `${bar.value}%` }} />
          ))}
        </div>

        <div className="mt-[10px] flex justify-between text-[22px] text-[#6a6a6a]">
          <p>Current margin: April Spendings</p>
          <p style={{ color: "#15342D" }}>$350.00 / $640.00</p>
        </div>
      </div>

      <div className="absolute right-[42px] top-[380px] h-[148px] w-[360px] bg-[#ececee] px-[34px] py-[22px]">
        <div className="flex items-center gap-[14px]">
          <div className="flex h-[48px] w-[48px] items-center justify-center rounded-full bg-[#0a3f73]">
            <img
              src={metricIcon?.__icon_url__}
              alt={metricIcon?.__icon_query__}
              className="h-[22px] w-[22px] object-contain"
              style={{ filter: "brightness(0) invert(1)" }}
            />
          </div>
          <p className="text-[28px] font-normal  text-[#15342DCC]" style={{ color: "#15342D" }}>
            {metricValue}
          </p>
        </div>
        <p className="mt-[16px] text-[28px] font-normal  text-[#15342DCC]" style={{ color: "#15342D" }}>
          {metricBody}
        </p>
      </div>
    </div>
  );
};

export default ReportSnapshotSlide;
