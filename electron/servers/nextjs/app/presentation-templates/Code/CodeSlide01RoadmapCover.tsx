import * as z from "zod";

export const slideLayoutId = "code-roadmap-cover-slide";
export const slideLayoutName = "Code Roadmap Cover Slide";
export const slideLayoutDescription =
  "A centered opening slide with company name, roadmap title, and supporting subtitle.";

export const Schema = z.object({
  companyName: z.string().min(2).max(28).default("COMPANY NAME").meta({
    description: "Organization name shown above the slide title.",
  }),
  title: z.string().min(8).max(28).default("Development Roadmap").meta({
    description: "Primary slide heading.",
  }),
  subtitle: z
    .string()
    .min(24)
    .max(92)
    .default(
      "We transform ideas into market-ready solutions through systematic development processes."
    )
    .meta({
      description: "Supporting subtitle shown under the heading.",
    }),
  pageLabel: z.string().min(3).max(8).default("1 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide01RoadmapCover = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden  bg-[#101B37] p-[53px] ">
      <div className="relative z-10 flex h-full flex-col items-center justify-center px-[200px] text-center">
        <p className="text-[22px]  text-[#d7dcff]">{data.companyName}</p>
        <h2 className="mt-[10px] text-[64px] font-medium  text-white">
          {data.title}
        </h2>
        <p className="mt-[35px] text-[26px] leading-[132%] text-[#d8ddff]">{data.subtitle}</p>
      </div>

      <div className="absolute bottom-[26px] left-1/2 -translate-x-1/2 rounded-full border border-[#31415880] bg-[#1D293DCC] px-[22px] py-[8px] text-[14px] text-[#CAD5E2]">
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide01RoadmapCover;
