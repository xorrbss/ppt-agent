import * as z from "zod";

export const slideLayoutId = "statistics-grid-slide";
export const slideLayoutName = "통계 그리드 슬라이드";
export const slideLayoutDescription =
  "제목과 설명이 담긴 왼쪽 텍스트 열과, 각 카드에 수치와 라벨이 들어간 오른쪽 통계 카드 그리드로 구성된 레이아웃.";

const StatisticSchema = z.object({
  value: z.string().max(8).meta({
    description: "Main metric value shown at the top of one card.",
  }),
  label: z.string().max(45).meta({
    description: "Label shown under the value.",
  }),
});

export const Schema = z.object({
  title: z.string().max(16).default("통계").meta({
    description: "Main title shown in the left column.",
  }),
  description: z.string().max(160).default(
    "주요 통계 지표를 통해 저희 조직의 규모와 역량을 한눈에 확인하실 수 있습니다. 각 수치는 지속적인 성장과 끊임없는 노력의 결과를 보여 줍니다."
  ).meta({
    description: "Supporting line shown under the left title.",
  }),
  stats: z
    .array(StatisticSchema)
    .min(2)
    .max(8)
    .default([
      { value: "120", label: "영업팀 규모, 레이아웃 테스트를 위한 긴 라벨" },
      { value: "15", label: "선임 영업 담당자, 레이아웃 테스트용 긴 라벨" },
      { value: "1", label: "전국 관리자, 레이아웃 테스트용 긴 라벨" },
      { value: "25", label: "영업 담당자, 레이아웃 테스트용 긴 라벨" },
      { value: "2", label: "지역 관리자, 레이아웃 테스트용 긴 라벨" },
      { value: "50", label: "총판 담당자, 레이아웃 테스트용 긴 라벨" },
      { value: "5", label: "권역 관리자, 레이아웃 테스트용 긴 라벨" },
      { value: "20", label: "머천다이징팀, 레이아웃 테스트용 긴 라벨" },
    ])
    .meta({
      description: "statistic cards, with value and label each in a card",
    }),

});

export type SchemaType = z.infer<typeof Schema>;



const EducationStatisticsGridSlide = ({ data }: { data: Partial<SchemaType> }) => {


  return (
    <>

      <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden"
        style={{
          backgroundColor: "var(--background-color,#efeff1)",
          fontFamily: "var(--body-font-family,'Source Serif 4')",
        }}
      >
        <div className="relative z-10 grid h-full grid-cols-[490px_1fr]">
          <div className="px-[44px] pb-[78px] pt-[96px]">
            <div className="flex h-full flex-col justify-end">
              <h2 className="font-serif text-[64px] leading-[98%] tracking-[-0.02em]" style={{ color: "var(--primary-color,#1a1752)" }}>
                {data.title}
              </h2>
              <p className="mt-[40px] max-w-[330px] text-[22px] leading-[1.24]" style={{ color: "var(--background-text,#34394C)" }}>
                {data.description}
              </p>
            </div>
          </div>

          {data.stats && data.stats?.length <= 4 && <div className="grid h-full grid-cols-1">
            {data.stats?.map((stat, index) => (
              <div
                key={`${stat.value}-${index}`}
                className="px-[52px] pt-[22px]"
                style={{ backgroundColor: index % 2 === 1 ? 'var(--card-color,#5C0FD908)' : 'var(--card-color,white)' }}
              >
                <p className="font-serif text-[58px] leading-[56px]" style={{ color: "var(--background-text,#434A63)" }}>
                  {stat?.value}
                </p>
                <p className="mt-[12px] text-[24px]" style={{ color: "var(--background-text,#434A63)" }}>
                  {stat?.label}
                </p>
              </div>
            ))}
          </div>}



          {data.stats && data.stats?.length > 4 && data.stats?.length <= 8 && (() => {
            // const rightArray = data.stats?.slice(0, Math.floor(data.stats?.length / 2));
            // const leftArray = data.stats?.slice(Math.floor(data.stats?.length / 2));

            return (
              <div className="h-full grid grid-cols-2 w-full">
                {/* <div className="flex flex-col h-full flex-1"> */}

                {data.stats?.map((stat: any, index: number) => (
                  <div
                    key={`${stat?.value}-${index}`}
                    className="px-[52px] pt-[22px] h-full"
                    style={{
                      backgroundColor: [0, 3, 4, 7].includes(index)
                        ? 'var(--card-color,#5C0FD908)'
                        : 'var(--card-color,white)'
                    }}
                  >
                    <p className=" text-[58px] leading-[56px]" style={{ color: "var(--background-text,#283E51)" }}>
                      {stat?.value}
                    </p>
                    <p className="mt-[12px] text-[24px]" style={{ color: "var(--background-text,#434A63)" }}>
                      {stat?.label}
                    </p>
                  </div>
                ))}

                {/* </div> */}

                {/* <div className="flex flex-col  flex-1">

                  {rightArray?.map((stat: any, index: number) => (
                    <div
                      key={`${stat.value}-${index}`}
                      className="px-[52px] pt-[22px] h-full"
                      style={{ backgroundColor: index % 2 === 1 ? 'var(--card-color,#5C0FD908)' : 'var(--card-color,white)' }}
                    >
                      <p className=" text-[58px] leading-[56px]" style={{ color: "var(--background-text,#283E51)" }}>
                        {stat.value}
                      </p>
                      <p className="mt-[12px] text-[24px]" style={{ color: "var(--background-text,#434A63)" }}>
                        {stat.label}
                      </p>
                    </div>
                  ))}
                </div> */}
              </div>
            );
          })()}
        </div>
      </div>
    </>
  );
};

export default EducationStatisticsGridSlide;
