import * as z from "zod";
import React from "react";

export const Schema = z.object({
  title: z
    .string()
    .max(12)
    .describe("The main title of the slide displayed at the top left")
    .default("표"),
  description: z
    .string()
    .max(180)
    .describe("The overview description paragraph displayed at the top right")
    .default(
      "금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 CAC를 150달러 미만으로 유지하며 350만 달러의 신규 파이프라인을 목표로 합니다."
    ),
  table: z
    .object({
      columns: z
        .array(z.string().max(15))
        .max(3)
        .describe("The headers for the table columns"),
      rows: z
        .array(z.array(z.string().max(60)).max(3))
        .max(3)
        .describe("The data rows for the table with max 3 cells per row"),
    })
    .describe("The main table content with headings and cell data")
    .default({
      columns: ["문제", "설명", "해결책"],
      rows: [
        [
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
        ],
        [
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
        ],
        [
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
          "자기 동기부여\n참고: 도서 및 영감을 주는 영상",
        ],
      ],
    }),
});
export const layoutId = "title-description-three-column-table";
export const layoutName = "제목 설명 3열 표";
export const layoutDescription =
  "왼쪽에 큰 헤더 제목, 오른쪽에 설명 문단을 배치하고 그 아래에 색상 헤더 행이 있는 3열 표를 표시하는 구조화된 데이터 슬라이드입니다. 각 표 셀은 여러 줄 텍스트를 지원합니다. 구조화된 비교, 문제-해결 매트릭스, 기능 분석 또는 시각적으로 정리하면 좋은 표 데이터를 표현하는 데 적합합니다.";

const dynamicSlideLayout: React.FC<{
  data: Partial<z.infer<typeof Schema>>;
}> = ({ data }) => {
  const { title, description, table } = data;
  const columns = table?.columns || [];
  const rows = table?.rows || [];

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />
      <div
        className="relative w-full h-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden font-['Albert_Sans']"
        style={{
          backgroundColor: "var(--background-color,#FFFFFF)",
          fontFamily: "var(--body-font-family,Albert Sans)",
        }}
      >
        {/* Slide Layout Container */}
        <div className="flex flex-col h-full px-[72px] pt-[65px] pb-[40px]">
          {/* Header Section */}
          <div className="flex justify-between items-start mb-[40px]">
            <h1
              className="text-[42.7px] text-black  font-bold leading-none uppercase tracking-[-1.6px]"
              style={{ color: "var(--background-text,#000000)" }}
            >
              {title}
            </h1>
            <p
              className="text-[16px] text-black leading-[1.6] max-w-[510px] text-left"
              style={{ color: "var(--background-text,#000000)" }}
            >
              {description}
            </p>
          </div>

          {/* Table Section */}
          <div
            className=" mx-auto"
            style={{ width: columns.length === 1 ? "60%" : "100%" }}
          >
            <div
              className="rounded-[4px] overflow-hidden bg-[#EDFAFD]"
              style={{ backgroundColor: "var(--card-color,#EDFAFD)" }}
            >
              <table className="w-full table-fixed border-separate border-spacing-0">
                <thead
                  style={{
                    backgroundColor: "var(--primary-color,#BEF4FE)",
                    width: "100%",
                  }}
                >
                  <tr className="h-[63.5px]">
                    {columns.map((column, index) => (
                      <th
                        key={index}
                        scope="col"
                        className="px-6 text-[21.4px] text-black font-normal text-center align-middle"
                        style={{ color: "var(--primary-text,#000000)" }}
                      >
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="h-[110px]">
                      {Array.from(
                        { length: columns.length },
                        (_, cellIndex) => (
                          <td
                            key={cellIndex}
                            className={`px-6 text-[20.3px] text-black text-center whitespace-pre-line leading-[1.4] align-middle ${
                              rowIndex < rows.length - 1
                                ? "border-b-[2.7px] border-[#EBEBEB]"
                                : ""
                            }`}
                            style={{
                              color: "var(--background-text,#000000)",
                              borderColor: "var(--stroke,#EBEBEB)",
                            }}
                          >
                            {row[cellIndex] || ""}
                          </td>
                        )
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* Bottom line for the table container if there are rows */}
              {rows.length > 0 && (
                <div
                  className="h-[2.7px] w-full bg-[#EBEBEB]"
                  style={{ backgroundColor: "var(--stroke,#EBEBEB)" }}
                />
              )}
            </div>
          </div>
        </div>

        {/* Footer Section */}
        <div className="flex items-center px-[72px] w-full absolute bottom-4 ">
          {((data as any)?.__companyName__ || (data as any)?._logo_url__) && (
            <div className="flex items-center gap-1 mr-1">
              {(data as any)?._logo_url__ && (
                <img
                  src={(data as any)?._logo_url__}
                  alt="logo"
                  className="w-[60px] object-contain"
                />
              )}
              <span
                style={{ backgroundColor: "var(--stroke, #F0F0F0)" }}
                className=" w-[2px] h-4"
              ></span>
              {(data as any)?.__companyName__ && (
                <span
                  className="text-sm  font-semibold"
                  style={{ color: "var(--background-text, #111827)" }}
                >
                  {(data as any)?.__companyName__ || "회사명"}
                </span>
              )}
            </div>
          )}
          <div
            className="flex-1 h-[3.6px] bg-[#55626E]"
            style={{ backgroundColor: "var(--background-text,#55626E)" }}
          />
          <div className="relative ml-[-4px] w-[58px] h-[58px] flex items-center justify-center">
            <div
              className="w-[41px] h-[41px] bg-[#4D5463] rotate-45"
              style={{ backgroundColor: "var(--background-text,#4D5463)" }}
            />
          </div>
        </div>
      </div>
    </>
  );
};

export default dynamicSlideLayout;
