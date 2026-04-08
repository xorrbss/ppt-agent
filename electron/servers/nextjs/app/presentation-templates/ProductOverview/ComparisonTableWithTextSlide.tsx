import * as z from "zod";

export const slideLayoutId = "product-overview-comparison-table-with-text-slide";
export const slideLayoutName = "Product Overview Comparison Table With Text Slide";
export const slideLayoutDescription =
  "A comparison table slide with a title, a subtitle, four headers, and three text rows.";

const RowSchema = z.object({
  cell1: z.string().max(12).meta({
    description: "First column cell text.",
  }),
  cell2: z.string().max(12).meta({
    description: "Second column cell text.",
  }),
  cell3: z.string().max(12).meta({
    description: "Third column cell text.",
  }),
  cell4: z.string().max(12).meta({
    description: "Fourth column cell text.",
  }),
});

export const Schema = z.object({
  title: z.string().min(8).max(20).default("Comparison Chart").meta({
    description: "Main heading shown above the table.",
  }),
  subtitle: z
    .string()
    .max(80)
    .default(
      "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt."
    )
    .meta({
      description: "Short subtitle shown under the main heading.",
    }),
  columns: z
    .array(z.string().max(10))
    .max(4)
    .default(["HEADING 1", "HEADING 1", "HEADING 2", "HEADING 3"])
    .meta({
      description: "Four table column headings.",
    }),
  highlightedHeaderIndex: z.number().int().min(1).max(4).default(4).meta({
    description: "1-based column index for the dark highlighted table header.",
  }),
  rows: z
    .array(RowSchema)
    .min(3)
    .max(3)
    .default([
      {
        cell1: "Lorem ipsum dolor sit.",
        cell2: "Lorem ipsum dolor sit.",
        cell3: "Lorem ipsum dolor sit.",
        cell4: "Lorem ipsum dolor sit.",
      },
      {
        cell1: "Lorem ipsum dolor sit.",
        cell2: "Lorem ipsum dolor sit.",
        cell3: "Lorem ipsum dolor sit.",
        cell4: "Lorem ipsum dolor sit.",
      },
      {
        cell1: "Lorem ipsum dolor sit.",
        cell2: "Lorem ipsum dolor sit.",
        cell3: "Lorem ipsum dolor sit.",
        cell4: "Lorem ipsum dolor sit.",
      },
    ])
    .meta({
      description: "Three table rows of text content.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const ComparisonTableWithTextSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, subtitle, columns, highlightedHeaderIndex, rows } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px]"
      style={{
        backgroundColor: "var(--background-color,#c3cccc)",
        fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
      }}
    >
      <div className="px-[44px] pt-[46px]">
        <h2
          className="text-[80px] font-semibold leading-[1.02] tracking-[-0.03em] text-[#0a443b]"
          style={{ color: "var(--primary-color,#0a443b)" }}
        >
          {title}
        </h2>
        <p
          className="mt-[22px] max-w-[700px] text-[24px] leading-[1.22] text-[#2d5d56]"
          style={{ color: "var(--background-text,#2d5d56)" }}
        >
          {subtitle}
        </p>
      </div>

      <div
        className="mx-[44px] mt-[30px] overflow-hidden border"
        style={{ borderColor: "var(--stroke,#bcc3c3)" }}
      >
        <table
          className="w-full table-fixed border-collapse"
          style={{ backgroundColor: "var(--card-color,#ffffff)" }}
        >
          <thead className="w-full">
            <tr className="w-full">
              {columns?.map((column, index) => {
                const isHighlighted = index + 1 === highlightedHeaderIndex;
                return (
                  <th
                    key={`${column}-${index}`}
                    className=" border-r p-[33px]  text-left text-[20px] font-semibold uppercase tracking-[0.16em] last:border-r-0"
                    style={{
                      borderColor: "var(--stroke,#bcc3c3)",
                      backgroundColor: isHighlighted
                        ? "var(--primary-color,#05443a)"
                        : "var(--card-color,#ffffff)",
                      color: isHighlighted
                        ? "var(--primary-text,#eef2f0)"
                        : "var(--primary-color,#123f38)",
                    }}
                  >
                    {column}
                  </th>
                );
              })}
            </tr>
          </thead>

          <tbody>
            {rows?.map((row, rowIndex) => {
              const cells = [row.cell1, row.cell2, row.cell3, row.cell4];
              const isHighlighted = rowIndex + 1 === highlightedHeaderIndex;

              return (
                <tr key={`row-${rowIndex}`}>
                  {cells?.map((cell, cellIndex) => (
                    <td
                      key={`cell-${rowIndex}-${cellIndex}`}
                      className=" border-r border-t bg-white p-[33px] text-left text-[18px] leading-[1.2] last:border-r-0"
                      style={{
                        borderColor: "var(--stroke,#bcc3c3)",
                        backgroundColor: isHighlighted
                          ? "var(--primary-color,#05443a)"
                          : "var(--card-color,#ffffff)",
                        color: isHighlighted
                          ? "var(--primary-text,#eef2f0)"
                          : "var(--primary-color,#123f38)",
                      }}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ComparisonTableWithTextSlide;
