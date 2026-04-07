import * as z from "zod";


export const slideLayoutId = "education-report-donut-slide";
export const slideLayoutName = "Education Report Donut Slide";
export const slideLayoutDescription =
  "A report slide with left-side title/content and a right-side donut chart with legend values.";

const SegmentSchema = z.object({
  label: z.string().min(3).max(12).meta({
    description: "Legend label for one donut chart segment.",
  }),
  value: z.number().min(1).max(100).meta({
    description: "Percentage value for one chart segment.",
  }),
  color: z.string().min(4).max(20).meta({
    description: "Hex color value for one chart segment.",
  }),
});

export const Schema = z.object({
  title: z.string().min(3).max(14).default("Report").meta({
    description: "Main heading in the left content area.",
  }),
  body: z.string().min(80).max(220).default(
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."
  ).meta({
    description: "Main report paragraph on the left.",
  }),
  footnote: z.string().min(20).max(110).default(
    "(Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt.)"
  ).meta({
    description: "Footnote text shown at the bottom of the left area.",
  }),
  chartTitle: z.string().min(8).max(26).default("Students by Grade Level").meta({
    description: "Heading shown above the donut chart.",
  }),
  dateRange: z.string().min(8).max(22).default("Apr 10 - Apr 17").meta({
    description: "Date range label under the chart heading.",
  }),
  segments: z
    .array(SegmentSchema)
    .min(4)
    .max(4)
    .default([
      { label: "Option A", value: 17.07, color: "#4A15A8" },
      { label: "Option B", value: 45.23, color: "#5B45AD" },
      { label: "Option C", value: 21.61, color: "#876FC1" },
      { label: "Option D", value: 16.36, color: "#A89ACF" },
    ])
    .meta({
      description: "Four donut segments with labels and percentages.",
    }),

});

export type SchemaType = z.infer<typeof Schema>;

const EducationReportDonutSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, body, footnote, chartTitle, dateRange, segments } = data;

  const total = segments?.reduce((sum, item) => sum + item.value, 0) || 0;
  let cursor = 0;
  const conicStops = segments
    ?.map((segment) => {
      const start = cursor;
      const span = total > 0 ? (segment.value / total) * 100 : 0;
      cursor += span;
      return `${segment.color} ${start}% ${cursor}%`;
    })
    .join(", ");

  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden bg-[#efeff1]">
      <div className="h-[147px] w-[147px] mx-[40px] bg-[#2C3592] p-[20px]">
      </div>
      <div className="grid h-full grid-cols-[1fr_600px]">


        <div className="px-[53px] flex flex-col justify-between">

          <div>
            <h2 className="mt-[131px] font-serif text-[64px] leading-[98%] tracking-[-0.02em] text-[#1a1752]">
              {title}
            </h2>

            <p className="mt-[30px] max-w-[610px] text-[22px] font-medium leading-[1.24] text-[#34394C]">
              {body}
            </p>
          </div>

          <p className=" max-w-[620px] text-[18px] leading-[1.28] text-[#46474C]">
            {footnote}
          </p>
        </div>

        <div className="px-[56px] pt-[106px]">
          <h3 className="text-center text-[24px] font-medium leading-none text-[#34394C]">
            {chartTitle}
          </h3>
          <p className="mt-[12px] text-center text-[14px] leading-none text-[#454962]">{dateRange}</p>

          <div className="mt-[28px] flex justify-center">
            <div
              className="relative h-[300px] w-[300px] rounded-full"
              style={{ background: `conic-gradient(${conicStops})` }}
            >
              <div className="absolute left-1/2 top-1/2 h-[222px] w-[222px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#efeff1]" />
            </div>
          </div>

          <div className="mt-[52px] grid grid-cols-2 gap-x-[34px] gap-y-[26px]">
            {segments?.map((segment, index) => (
              <div key={`${segment.label}-${index}`} className="flex items-center gap-[14px]">
                <span className="h-[18px] w-[18px] rounded-full" style={{ backgroundColor: segment.color }} />
                <span className="text-[20px] leading-none text-[#46474C]">{segment.label}</span>
                <span className="text-[20px] font-medium leading-none text-[#34394C]">
                  {segment.value.toFixed(2)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

    </div>
  );
};

export default EducationReportDonutSlide;
