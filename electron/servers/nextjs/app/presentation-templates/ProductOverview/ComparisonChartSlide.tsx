import * as z from "zod";


export const slideLayoutId = "product-overview-comparison-chart-slide";
export const slideLayoutName = "Product Overview Comparison Chart Slide";
export const slideLayoutDescription =
  "A comparison table slide with a headline, short description, four column headers, and three data rows using check, cross, or empty cells.";

const CellStatusSchema = z.enum(["check", "cross", "empty"]);

const RowSchema = z.object({
  label: z.string().min(4).max(18).meta({
    description: "Row heading shown in the first column.",
  }),
  cell1: CellStatusSchema.default("check"),
  cell2: CellStatusSchema.default("empty"),
  cell3: CellStatusSchema.default("check"),
  cell4: CellStatusSchema.default("empty"),
});

export const Schema = z.object({
  title: z.string().min(8).max(20).default("Comparison Chart").meta({
    description: "Main heading shown above the table.",
  }),
  subtitle: z.string().max(80).default(
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt."
  ).meta({
    description: "Short subtitle shown under the main heading.",
  }),
  columns: z
    .array(z.string().min(4).max(18))
    .min(4)
    .max(4)
    .default(["HEADING 1", "HEADING 1", "HEADING 2", "HEADING 3"])
    .meta({
      description: "Four table column headings.",
    }),
  rows: z
    .array(RowSchema)

    .max(3)
    .default([
      {
        label: "HEADING 1",
        cell1: "check",
        cell2: "cross",
        cell3: "check",
        cell4: "cross",
      },
      {
        label: "HEADING 1",
        cell1: "check",
        cell2: "empty",
        cell3: "check",
        cell4: "empty",
      },
      {
        label: "HEADING 2",
        cell1: "check",
        cell2: "check",
        cell3: "check",
        cell4: "check",
      },
    ])
    .meta({
      description: "Three table rows with status indicators.",
    }),
  checkIcon: z.object({
    __icon_url__: z.string(),
    __icon_query__: z.string(),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "check icon",
  }).meta({
    description: "Icon used for positive comparison status.",
  }),
  crossIcon: z.object({
    __icon_url__: z.string(),
    __icon_query__: z.string(),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "cross icon",
  }).meta({
    description: "Icon used for negative comparison status.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

function StatusIcon({
  status,
  checkIconUrl,
  checkIconAlt,
  crossIconUrl,
  crossIconAlt,
}: {
  status: "check" | "cross" | "empty";
  checkIconUrl: string | undefined;
  checkIconAlt: string | undefined;
  crossIconUrl: string | undefined;
  crossIconAlt: string | undefined;
}) {
  if (status === "empty") {
    return <span className="h-[26px] w-[26px]" />;
  }

  if (status === "cross") {
    return <img src={crossIconUrl} alt={crossIconAlt} className="h-[26px] w-[26px] object-contain" />;
  }

  return <img src={checkIconUrl} alt={checkIconAlt} className="h-[26px] w-[26px] object-contain" />;
}

const ComparisonChartSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, subtitle, columns, rows, checkIcon, crossIcon } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px]"
        style={{
          backgroundColor: "var(--background-color,#DAE1DE)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className="px-[56px] pt-[74px]">
          <h2
            className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {title}
          </h2>
          <p
            className="mt-[20px] w-[740px] text-[24px] font-normal  text-[#15342DCC]"
            style={{ color: "var(--background-text,#15342DCC)" }}
          >
            {subtitle}
          </p>
        </div>

        <div className="absolute left-[54px] top-[268px] w-[1058px] ">
          <div
            className="grid grid-cols-[220px_repeat(4,1fr)] border-b"
            style={{ borderColor: "var(--stroke,#c5cccb)" }}
          >
            <div className="h-[94px] " />
            {columns?.map((column, index) => (
              <div
                key={index}
                className="flex h-[94px] items-center px-[33px] justify-center border-r text-[20px] font-semibold uppercase tracking-[0.2em]"
                style={{
                  backgroundColor:
                    index === 3
                      ? "var(--primary-color,#15342D)"
                      : "var(--card-color,#ffffff)",
                  color:
                    index === 3
                      ? "var(--primary-text,#edf2f1)"
                      : "var(--primary-color,#15342D)",
                  borderColor: "var(--stroke,#c5cccb)",
                }}
              >
                {column}
              </div>
            ))}
          </div>

          {rows?.map((row, index) => {
            const cells: ("check" | "cross" | "empty")[] = [
              row.cell1,
              row.cell2,
              row.cell3,
              row.cell4,
            ];

            return (
              <div
                key={index}
                className={`grid grid-cols-[220px_repeat(4,1fr)] ${index < rows.length - 1 ? "border-b" : ""}`}
                style={{ borderColor: "var(--stroke,#c5cccb)" }}
              >
                <div
                  className="flex  items-center border-r pl-[34px] text-[20px] font-semibold uppercase tracking-[0.2em]"
                  style={{
                    backgroundColor: "var(--card-color,#ffffff)",
                    borderColor: "var(--stroke,#c5cccb)",
                    color: "var(--primary-color,#15342D)",
                  }}
                >
                  {row.label}
                </div>

                {cells?.map((status, cellIndex) => (
                  <div
                    key={cellIndex}
                    className="flex  p-[33px] items-center justify-center border-r"
                    style={{
                      backgroundColor: "var(--card-color,#ffffff)",
                      borderColor: "var(--stroke,#c5cccb)",
                    }}
                  >
                    <StatusIcon
                      status={status}
                      checkIconUrl={checkIcon?.__icon_url__}
                      checkIconAlt={checkIcon?.__icon_query__}
                      crossIconUrl={crossIcon?.__icon_url__}
                      crossIconAlt={crossIcon?.__icon_query__}
                    />
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
};

export default ComparisonChartSlide;
