import * as z from "zod";

export const slideLayoutId = "title-description-with-table-slide";
export const slideLayoutName = "제목·설명과 표 슬라이드";
export const slideLayoutDescription =
  "제목, 부제목, 열 헤더, 그리고 텍스트 내용 행으로 구성된 비교 표 슬라이드.";

const TableCellSchema = z.string().max(40).meta({
  description: "Table cell text.",
});

const GeneralRowSchema = z.object({
  cells: z.array(TableCellSchema).min(1).max(8).meta({
    description: "Row cell values matching the table columns.",
  }),
});

const LegacyRowSchema = z.object({
  cell1: TableCellSchema.optional(),
  cell2: TableCellSchema.optional(),
  cell3: TableCellSchema.optional(),
  cell4: TableCellSchema.optional(),
});

const RowSchema = z.union([GeneralRowSchema, LegacyRowSchema]);

const DEFAULT_COLUMNS = ["제목 1", "제목 1", "제목 2", "제목 3"];
const DEFAULT_ROWS: z.infer<typeof GeneralRowSchema>[] = [
  {
    cells: [
      "예시 내용입니다.",
      "예시 내용입니다.",
      "예시 내용입니다.",
      "예시 내용입니다.",
    ],
  },
  {
    cells: [
      "예시 내용입니다.",
      "예시 내용입니다.",
      "예시 내용입니다.",
      "예시 내용입니다.",
    ],
  },
  {
    cells: [
      "예시 내용입니다.",
      "예시 내용입니다.",
      "예시 내용입니다.",
      "예시 내용입니다.",
    ],
  },
];

export const Schema = z.object({
  title: z.string().max(14).default("비교 차트").meta({
    description: "Main heading shown above the table.",
  }),
  subtitle: z
    .string()
    .max(80)
    .default(
      "예시 부제목입니다. 각 항목을 한눈에 비교할 수 있도록 도와주는 자리 표시 문구입니다."
    )
    .meta({
      description: "Short subtitle shown under the main heading.",
    }),
  columns: z
    .array(z.string().max(20))
    .min(1)
    .max(8)
    .default(DEFAULT_COLUMNS)
    .meta({
      description: "Table column headings.",
    }),
  highlightedHeaderIndex: z.number().int().min(1).max(8).default(4).meta({
    description: "1-based column index for the dark highlighted table header.",
  }),
  rows: z
    .array(RowSchema)
    .min(1)
    .max(6)
    .default(DEFAULT_ROWS)
    .meta({
      description: "Table rows of text content. Prefer the `cells` array format.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const ComparisonTableWithTextSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, subtitle, columns, highlightedHeaderIndex, rows } = data;
  const safeColumns = columns && columns.length > 0 ? columns : DEFAULT_COLUMNS;
  const resolvedHighlightedHeaderIndex =
    highlightedHeaderIndex &&
      highlightedHeaderIndex >= 1 &&
      highlightedHeaderIndex <= safeColumns.length
      ? highlightedHeaderIndex
      : Math.min(4, safeColumns.length);
  const safeRows = rows && rows.length > 0 ? rows : DEFAULT_ROWS;
  const normalizedRows = safeRows.map((row) => {
    const rowCells =
      "cells" in row
        ? row.cells
        : [row.cell1, row.cell2, row.cell3, row.cell4].filter(
          (cell): cell is string => typeof cell === "string"
        );

    return Array.from(
      { length: safeColumns.length },
      (_, cellIndex) => rowCells[cellIndex] ?? ""
    );
  });

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#c3cccc)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className="px-[44px] pt-[50px]">
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
                {safeColumns.map((column, index) => {
                  const isHighlighted = index + 1 === resolvedHighlightedHeaderIndex;
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
              {normalizedRows.map((cells, rowIndex) => {
                return (
                  <tr key={`row-${rowIndex}`}>
                    {cells?.map((cell, cellIndex) => (
                      <td
                        key={`cell-${rowIndex}-${cellIndex}`}
                        className=" border-r border-t bg-white p-[33px] text-left text-[18px] leading-[1.2] last:border-r-0"
                        style={{
                          borderColor: "var(--stroke,#bcc3c3)",
                          backgroundColor: "var(--card-color,#ffffff)",
                          color: "var(--primary-color,#123f38)",
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
    </>
  );
};

export default ComparisonTableWithTextSlide;
