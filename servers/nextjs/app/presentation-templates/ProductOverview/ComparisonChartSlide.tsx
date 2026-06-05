import * as z from "zod";

export const slideLayoutId = "title-description-with-table-slide";
export const slideLayoutName = "제목·설명과 표 슬라이드";
export const slideLayoutDescription =
  "상단에 제목과 그 아래 설명이 있고, 열 헤더와 체크·엑스·빈 상태의 행으로 구성된 표가 담긴 콘텐츠 섹션이 있는 슬라이드.";

const CellStatusSchema = z.enum(["check", "cross", "empty"]);

const GeneralRowSchema = z.object({
  label: z.string().max(18).meta({
    description: "Row heading shown in the first column.",
  }),
  cells: z.array(CellStatusSchema).min(1).max(8).meta({
    description: "Status cells aligned with the table columns.",
  }),
});

const LegacyRowSchema = z.object({
  label: z.string().max(18).meta({
    description: "Row heading shown in the first column.",
  }),
  cell1: CellStatusSchema.optional(),
  cell2: CellStatusSchema.optional(),
  cell3: CellStatusSchema.optional(),
  cell4: CellStatusSchema.optional(),
});

const RowSchema = z.union([GeneralRowSchema, LegacyRowSchema]);

export const Schema = z.object({
  title: z.string().max(24).default("비교 차트").meta({
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
    .array(z.string().max(18))
    .min(1)
    .max(4)
    .default(["제목 1", "제목 2", "제목 3", "제목 4"])
    .meta({
      description: "Table column headings.",
    }),
  highlightedColumnIndex: z.number().int().min(1).max(8).default(4).meta({
    description: "1-based column index for the dark highlighted table header.",
  }),
  rows: z
    .array(RowSchema)
    .min(1)
    .max(3)
    .default([
      {
        label: "제목 1",
        cells: ["check", "cross", "check", "cross"],
      },
      {
        label: "제목 1",
        cells: ["check", "empty", "check", "empty"],
      },
      {
        label: "제목 2",
        cells: ["check", "check", "check", "check"],
      },
    ])
    .meta({
      description:
        "Table rows with status indicators. Prefer the `cells` array format.",
    }),
  checkIcon: z
    .object({
      __icon_url__: z.string(),
      __icon_query__: z.string(),
    })
    .default({
      __icon_url__:
        "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
      __icon_query__: "check icon",
    })
    .meta({
      description: "Icon used for positive comparison status.",
    }),
  crossIcon: z
    .object({
      __icon_url__: z.string(),
      __icon_query__: z.string(),
    })
    .default({
      __icon_url__:
        "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
      __icon_query__: "cross icon",
    })
    .meta({
      description: "Icon used for negative comparison status.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;
type CellStatus = z.infer<typeof CellStatusSchema>;

function StatusIcon({
  status,
  checkIconUrl,
  checkIconAlt,
  crossIconUrl,
  crossIconAlt,
}: {
  status: any;
  checkIconUrl: string | undefined;
  checkIconAlt: string | undefined;
  crossIconUrl: string | undefined;
  crossIconAlt: string | undefined;
}) {
  if (status === "empty") {
    return <span className="h-[26px] w-[26px]" />;
  }

  if (status === "cross") {
    return (
      <img
        src={crossIconUrl}
        alt={crossIconAlt}
        className="h-[26px] w-[26px] object-contain"
      />
    );
  }
  if (status === "check") {
    return (
      <img
        src={checkIconUrl}
        alt={checkIconAlt}
        className="h-[26px] w-[26px] object-contain"
      />
    );
  }
  return <p className="text-base ">{status}</p>;
}

const ComparisonChartSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const {
    title,
    subtitle,
    columns,
    highlightedColumnIndex,
    rows,
    checkIcon,
    crossIcon,
  } = data;
  const safeColumns = columns && columns.length > 0 ? columns : [];
  const resolvedHighlightedColumnIndex =
    highlightedColumnIndex &&
    highlightedColumnIndex >= 1 &&
    highlightedColumnIndex <= safeColumns.length
      ? highlightedColumnIndex
      : Math.min(4, safeColumns.length);
  const safeRows = rows && rows.length > 0 ? rows : [];
  const normalizedRows = safeRows.map((row) => {
    const rowCells =
      "cells" in row
        ? row.cells
        : [row.cell1, row.cell2, row.cell3, row.cell4].filter(
            (cell): cell is CellStatus => typeof cell !== "undefined"
          );

    return {
      label: row.label,
      cells: Array.from(
        { length: safeColumns.length },
        (_, cellIndex) => rowCells[cellIndex] ?? "empty"
      ),
    };
  });

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap"
        rel="stylesheet"
      />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#DAE1DE)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className="px-[56px] pt-[50px]">
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

        <div className="mx-[54px] mt-[20px] ">
          <table className="w-full table-fixed border-collapse">
            <colgroup>
              <col className="w-[220px]" />
              {safeColumns.map((_, index) => (
                <col key={index} />
              ))}
            </colgroup>
            <thead style={{ width: "100%" }}>
              <tr
                className="border-b"
                style={{
                  borderColor: "var(--stroke,#c5cccb)",
                }}
              >
                <th scope="col" aria-hidden="true" className="w-[220px]" />
                {safeColumns.map((column: any, index: any) => (
                  <th
                    key={index}
                    scope="col"
                    className="p-[33px] text-center align-middle border-r text-[20px] font-semibold tracking-[0.2em]"
                    style={{
                      backgroundColor:
                        index + 1 === resolvedHighlightedColumnIndex
                          ? "var(--primary-color,#15342D)"
                          : "var(--card-color,#ffffff)",
                      color:
                        index + 1 === resolvedHighlightedColumnIndex
                          ? "var(--primary-text,#edf2f1)"
                          : "var(--primary-color,#15342D)",
                      borderColor: "var(--stroke,#c5cccb)",
                    }}
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {normalizedRows.map((row, index) => {
                return (
                  <tr
                    key={index}
                    className={
                      index < normalizedRows.length - 1 ? "border-b" : ""
                    }
                    style={{
                      borderColor: "var(--stroke,#c5cccb)",
                    }}
                  >
                    <th
                      scope="row"
                      className="align-middle border-r pl-[34px] text-left text-[20px] font-semibold tracking-[0.2em]"
                      style={{
                        backgroundColor: "var(--card-color,#ffffff)",
                        borderColor: "var(--stroke,#c5cccb)",
                        color: "var(--primary-color,#15342D)",
                      }}
                    >
                      {row.label}
                    </th>

                    {row.cells.map((status, cellIndex) => (
                      <td
                        key={cellIndex}
                        className="p-[23px] text-center align-middle border-r"
                        style={{
                          backgroundColor: "var(--card-color,#ffffff)",
                          borderColor: "var(--stroke,#c5cccb)",
                        }}
                      >
                        <div className="flex items-center justify-center">
                          <StatusIcon
                            status={status}
                            checkIconUrl={checkIcon?.__icon_url__}
                            checkIconAlt={checkIcon?.__icon_query__}
                            crossIconUrl={crossIcon?.__icon_url__}
                            crossIconAlt={crossIcon?.__icon_query__}
                          />
                        </div>
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

export default ComparisonChartSlide;
