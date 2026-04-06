import * as z from "zod";

const ComparisonRowSchema = z.object({
  feature: z.string().min(4).max(20).meta({
    description: "Feature label shown in the first column.",
  }),
  react: z.string().min(1).max(12).meta({
    description: "React cell value.",
  }),
  vue: z.string().min(1).max(12).meta({
    description: "Vue cell value.",
  }),
  angular: z.string().min(1).max(12).meta({
    description: "Angular cell value.",
  }),
});

export const slideLayoutId = "code-comparison-table-slide";
export const slideLayoutName = "Code Comparison Table Slide";
export const slideLayoutDescription =
  "A framework comparison table with feature rows and highlighted compatibility marks.";

export const Schema = z.object({
  title: z.string().min(6).max(18).default("Comparison").meta({
    description: "Slide title shown above the table.",
  }),
  rows: z
    .array(ComparisonRowSchema)
    .min(6)
    .max(6)
    .default([
      { feature: "Component-based", react: "check", vue: "check", angular: "check" },
      { feature: "TypeScript Support", react: "check", vue: "check", angular: "check" },
      { feature: "Learning Curve", react: "Medium", vue: "Easy", angular: "Steep" },
      { feature: "Bundle Size", react: "40KB", vue: "34KB", angular: "167KB" },
      { feature: "Performance", react: "Excellent", vue: "Excellent", angular: "Good" },
      { feature: "Community Size", react: "Huge", vue: "Large", angular: "Large" },
    ])
    .meta({
      description: "Six comparison rows shown in the table.",
    }),
  pageLabel: z.string().min(3).max(8).default("5 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

function renderCell(value: string) {
  if (value.toLowerCase() === "check") {
    return <span className="text-[26px] px-[32px]  text-[#37f08e]">✓</span>;
  }

  return <span className="text-[18px]   px-[32px]  text-[#CAD5E2]">{value}</span>;
}

const CodeSlide05ComparisonTable = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden  bg-[#101B37] p-[53px] ">

      <h2 className="text-[64px] font-medium  text-[#ffffff]">{data.title}</h2>

      <div className="mt-[22px] min-h-0 flex-1 rounded-[16px]  bg-[#0F172BCC] border border-[#1D293D80]">
        <div className="grid grid-cols-[0.4fr_0.20fr_0.20fr_0.20fr] items-center   text-[#8ea1da]">
          <p className="px-[32px] py-[16px] text-[18px] text-center border-b border-r border-[#1D293D80]">Feature</p>
          <p className="px-[32px] py-[16px] text-[18px] text-center text-[#ffffff] border-b border-r border-[#1D293D80]">React</p>
          <p className="px-[32px] py-[16px] text-[18px] text-center text-[#ffffff] border-b border-r border-[#1D293D80]">Vue</p>
          <p className="px-[32px] py-[16px] text-[18px] text-center text-[#ffffff]  border-b border-r border-[#1D293D80]">Angular</p>
        </div>

        <div className="">
          {data?.rows?.map((row) => (
            <div
              key={row.feature}
              className="grid grid-cols-[0.4fr_0.20fr_0.20fr_0.20fr]   "
            >
              <p className="px-[32px] py-[20px] text-center text-[18px] text-[#d5dcff] border-b border-r border-[#1D293D80]">{row.feature}</p>
              <div className="flex justify-center items-center text-[18px]  border-b border-r border-[#1D293D80]  ">{renderCell(row.react)}</div>
              <div className="flex justify-center items-center text-[18px] border-b border-r border-[#1D293D80] ">{renderCell(row.vue)}</div>
              <div className="flex justify-center items-center text-[18px] border-b border-r border-[#1D293D80] ">{renderCell(row.angular)}</div>
            </div>
          ))}
        </div>

      </div>

      <div className="absolute bottom-[26px] z-50 left-1/2 -translate-x-1/2 rounded-full border border-[#31415880] bg-[#1D293DCC] px-[22px] py-[8px] text-[14px] text-[#CAD5E2]">
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide05ComparisonTable;
