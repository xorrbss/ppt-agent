import * as z from "zod";

const ComparisonRowSchema = z.object({
  feature: z.string().min(4).max(17).meta({
    description: "Feature label shown in the first column.",
  }),
  column1: z.string().max(10).meta({
    description: "Column 1 cell value.",
  }),
  column2: z.string().max(10).meta({
    description: "Column 2 cell value.",
  }),
  column3: z.string().max(10).meta({
    description: "Column 3 cell value.",
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
  tableColumns: z.array(z.string().max(4)).meta({
    description: "Table columns shown in the first row.",
  }).default(["Feature", "Column 1", "Column 2", "Column 3"]),
  rows: z
    .array(ComparisonRowSchema)
    .min(6)
    .max(6)
    .default([
      { feature: "Component-based", column1: "check", column2: "check", column3: "check" },
      { feature: "TypeScript Support", column1: "check", column2: "check", column3: "check" },
      { feature: "Learning Curve", column1: "Medium", column2: "Easy", column3: "Steep" },
      { feature: "Bundle Size", column1: "40KB", column2: "34KB", column3: "167KB" },
      { feature: "Performance", column1: "Excellent", column2: "Excellent", column3: "Good" },
      { feature: "Community Size", column1: "Huge", column2: "Large", column3: "Large" },
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
    return <span className="text-[26px] px-[32px]" style={{ color: "var(--graph-2,#37f08e)" }}>✓</span>;
  }

  return <span className="text-[18px]   px-[32px]" style={{ color: "var(--background-text,#CAD5E2)" }}>{value}</span>;
}

const CodeSlide05ComparisonTable = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden p-[53px]"
      style={{
        backgroundColor: "var(--background-color,#101B37)",
        fontFamily: "var(--body-font-family,Nunito Sans)",
      }}
    >

      <h2 className="text-[64px] font-medium" style={{ color: "var(--background-text,#ffffff)" }}>{data.title}</h2>

      <div
        className="mt-[22px] min-h-0 flex-1 rounded-[16px] border"
        style={{
          backgroundColor: "var(--card-color,#0F172BCC)",
          borderColor: "var(--stroke,#1D293D80)",
        }}
      >
        <div
          className="grid grid-cols-[0.4fr_0.20fr_0.20fr_0.20fr] items-center"
          style={{
            color: "var(--background-text,#8ea1da)",
            gridTemplateColumns: `repeat(${data?.tableColumns?.length || 1}, 1fr)`,
          }}
        >

          {data?.tableColumns?.map((column) => (
            <p
              key={column}
              className="px-[32px] py-[16px] text-[18px] text-center border-b border-r"
              style={{
                color: "var(--background-text,#ffffff)",
                borderColor: "var(--stroke,#1D293D80)",
              }}
            >
              {column}
            </p>
          ))}
        </div>

        <div className="">
          {data?.rows?.map((row) => (
            <div
              key={row.feature}
              className="grid grid-cols-[0.4fr_0.20fr_0.20fr_0.20fr]"
              style={{
                gridTemplateColumns: `repeat(${data?.tableColumns?.length || 1}, 1fr)`,
              }}
            >
              <p
                className="px-[32px] py-[20px] text-center text-[18px] border-b border-r"
                style={{
                  color: "var(--background-text,#d5dcff)",
                  borderColor: "var(--stroke,#1D293D80)",
                }}
              >
                {row.feature}
              </p>
              <div className="flex justify-center items-center text-[18px] border-b border-r" style={{ borderColor: "var(--stroke,#1D293D80)" }}>{renderCell(row.column1)}</div>
              <div className="flex justify-center items-center text-[18px] border-b border-r" style={{ borderColor: "var(--stroke,#1D293D80)" }}>{renderCell(row.column2)}</div>
              <div className="flex justify-center items-center text-[18px] border-b border-r" style={{ borderColor: "var(--stroke,#1D293D80)" }}>{renderCell(row.column3)}</div>
            </div>
          ))}
        </div>

      </div>

      <div
        className="absolute bottom-[26px] z-50 left-1/2 -translate-x-1/2 rounded-full border px-[22px] py-[8px] text-[14px]"
        style={{
          borderColor: "var(--stroke,#31415880)",
          backgroundColor: "var(--card-color,#1D293DCC)",
          color: "var(--background-text,#CAD5E2)",
        }}
      >
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide05ComparisonTable;
