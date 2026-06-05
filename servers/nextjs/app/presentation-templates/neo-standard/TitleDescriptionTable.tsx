import * as z from "zod";

export const Schema = z.object({
  title: z
    .string()
    .max(10)
    .describe("The main title of the slide")
    .default("표"),
  description: z
    .string()
    .max(250)
    .describe("The descriptive paragraph at the top right of the slide")
    .default(
      "금융 서비스, 헬스케어, 기술 분야의 직원 500명 이상 기업에 집중합니다. 어카운트 기반 마케팅과 콘텐츠 중심 전략으로 CAC 150달러 미만, 신규 파이프라인 350만 달러를 목표로 합니다."
    ),
  table: z
    .object({
      columns: z
        .array(z.string().max(15))
        .max(3)
        .describe("The headers for the table columns"),
      rows: z
        .array(z.array(z.string().max(80)).max(3))
        .max(3)
        .describe(
          "The data rows for the table, each containing multiple lines of text"
        ),
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
        ["자기 동기부여\n참고: 도서 및 영감을 주는 영상"],
        ["자기 동기부여\n참고: 도서 및 영감을 주는 영상"],
      ],
    }),
});

export const layoutId = "title-description-table";
export const layoutName = "제목 설명 표";
export const layoutDescription =
  "상단에 제목과 설명을 두고, 그 아래에 색상으로 강조된 헤더가 있는 3열 표를 배치한 슬라이드로, 비교, 매트릭스, 구조화된 데이터에 이상적입니다.";

const dynamicSlideLayout: React.FC<{
  data: Partial<z.infer<typeof Schema>>;
}> = ({ data }) => {
  const { title, description, table } = data;
  const columns = table?.columns || [];
  const rows = table?.rows || [];

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />
      <div
        className="relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-[#FFFFFE] z-20 mx-auto overflow-hidden flex flex-col justify-center p-[72px] pt-[65px]"
        style={{
          backgroundColor: "var(--background-color,#FFFFFF)",
          fontFamily: "var(--body-font-family,Playfair Display)",
        }}
      >
        {/* Header Section */}
        <div className="flex justify-between items-start gap-10 mb-[40px]">
          <div className="">
            <h1
              className="text-[42.7px] text-black  font-bold leading-none uppercase"
              style={{ color: "var(--background-text,#000000)" }}
            >
              {title}
            </h1>
          </div>
          <div className="max-w-[510px]">
            <p
              style={{
                fontFamily: "Playfair Display",
                color: "var(--background-text,#000000)",
              }}
              className="text-[16px] text-black leading-[1.6]"
            >
              {description}
            </p>
          </div>
        </div>

        {/* Table Section */}
        <div
          className="w-full mx-auto mt-4"
          style={{
            width: columns.length === 1 ? "60%" : "100%",
          }}
        >
          <table className="w-full table-fixed border-separate border-spacing-0 overflow-hidden rounded-t-sm">
            <thead
              style={{
                backgroundColor: "var(--primary-color,#1F8A2E)",
                width: "100%",
              }}
            >
              <tr>
                {columns.map((column, index) => (
                  <th
                    key={index}
                    scope="col"
                    style={{ color: "var(--primary-text,#FFFFFF)" }}
                    className="py-[18px] text-[21.4px] font-bold text-[#FFFFFE] text-center"
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {Array.from({ length: columns.length }, (_, cellIndex) => (
                    <td
                      key={cellIndex}
                      className={`text-[20.3px] py-3 px-2 text-black text-center whitespace-pre-line leading-[1.4] align-middle ${
                        rowIndex < rows.length - 1
                          ? "border-b border-[#EBEBEB]"
                          : ""
                      }`}
                      style={{
                        color: "var(--background-text,#000000)",
                        borderColor: "var(--stroke,#EBEBEB)",
                      }}
                    >
                      {row[cellIndex] || ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {((data as any)?.__companyName__ || (data as any)?._logo_url__) && (
          <div className="flex items-center gap-1 absolute bottom-5 left-5 z-40">
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
      </div>
    </>
  );
};

export default dynamicSlideLayout;
