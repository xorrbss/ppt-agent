import * as z from "zod";

export const Schema = z.object({
  title: z
    .string()
    .max(12)
    .describe("The main heading of the slide")
    .default("표"),
  description: z
    .string()
    .max(250)
    .describe("Supporting description text")
    .default(
      "금융 서비스, 헬스케어, 기술 분야에서 직원 500명 이상의 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략을 통해 CAC 150달러 미만으로 350만 달러의 신규 파이프라인을 목표로 합니다."
    ),
  table: z
    .object({
      columns: z
        .array(z.string().max(15))
        .max(3)
        .describe("Column headers for the table"),
      rows: z
        .array(z.array(z.string().max(60)).max(3))
        .max(3)
        .describe("Data rows for the table"),
    })
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

/**
 * Layout ID, Name and Description.
 */
export const layoutId = "title-description-table";
export const layoutName = "제목 설명 표";
export const layoutDescription =
  "굵은 제목, 설명, 그리고 색상으로 강조된 헤더가 있는 깔끔한 3열 표를 갖춘 슬라이드입니다. 헤더 행이 시각적 계층을 제공하며 둥근 셀 배경이 모던한 느낌을 유지합니다.";

/**
 * React Component for the slide layout.
 */
const dynamicSlideLayout: React.FC<{
  data: Partial<z.infer<typeof Schema>>;
}> = ({ data }) => {
  const { title, description, table } = data;
  const { columns, rows } = table || {};

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
        rel="stylesheet"
      />
      <div
        className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex flex-col p-[72px]"
        style={{
          backgroundColor: "var(--background-color,#FFFFFF)",
          fontFamily: "var(--body-font-family,Montserrat)",
        }}
      >
        {/* Header Section */}
        <div className="flex justify-between items-start mb-[45px]">
          <div className="w-[30%]">
            <h1
              className="text-[42.7px]  font-bold leading-tight tracking-[-1.6px] uppercase"
              style={{ color: "var(--background-text,#002BB2)" }}
            >
              {title}
            </h1>
          </div>
          <div className="w-[45%]">
            <p
              className="text-[16px]  font-normal leading-[1.6]"
              style={{ color: "var(--background-text,#002BB2)" }}
            >
              {description}
            </p>
          </div>
        </div>

        {/* Table Section */}
        <div
          className="flex flex-col gap-[17px] w-full mx-auto"
          style={{ width: columns?.length === 1 ? "60%" : "100%" }}
        >
          <table className="block w-full border-separate border-spacing-0">
            <thead className="block w-full">
              <tr
                className="bg-[#1F4CD9] h-[64px] w-full rounded-[4px] flex justify-between px-8 gap-[17px] items-center"
                style={{
                  backgroundColor: "var(--primary-color,#1F4CD9)",
                  width: "100%",
                }}
              >
                {columns?.map((column, index) => (
                  <th
                    key={index}
                    scope="col"
                    className="text-center w-full font-normal"
                  >
                    <span
                      className="text-[21.4px] font-bold"
                      style={{ color: "var(--primary-text,#FFFFFE)" }}
                    >
                      {column}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>

            <tbody className="mt-[17px] flex w-full flex-col gap-[17px]">
              {rows?.map((row, rowIndex) => (
                <tr
                  key={rowIndex}
                  className="flex w-full justify-between gap-[17px]"
                >
                  {Array.from(
                    { length: columns?.length || 0 },
                    (_, cellIndex) => {
                      const cell = row[cellIndex] || "";
                      return (
                        <td
                          key={cellIndex}
                          className="bg-[#F7F8FF] w-full rounded-[12px] h-[105px] px-6 text-center align-middle"
                          style={{
                            backgroundColor: "var(--card-color,#F7F8FF)",
                          }}
                        >
                          <div className="flex h-full flex-col justify-center items-center">
                            {cell.split("\n").map((line, lineIndex) => (
                              <span
                                key={lineIndex}
                                className="text-[20.3px] font-normal leading-[1.4]"
                                style={{
                                  color: "var(--background-text,#002BB2)",
                                }}
                              >
                                {line}
                              </span>
                            ))}
                          </div>
                        </td>
                      );
                    }
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {(data as any)?.__companyName__ ||
          ((data as any)?._logo_url__ && (
            <div className="flex items-center gap-1 absolute top-5 left-5 z-40">
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
          ))}
      </div>
    </>
  );
};
export default dynamicSlideLayout;
