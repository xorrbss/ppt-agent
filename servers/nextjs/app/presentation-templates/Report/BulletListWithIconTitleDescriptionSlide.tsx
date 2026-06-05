import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";
import * as z from "zod";


const AnalysisItemSchema = z.object({
  title: z.string().max(30).meta({
    description: "Short item title displayed next to the icon.",
  }),
  description: z.string().max(60).meta({
    description: "Supporting sentence shown below the title.",
  }),
});

export const slideLayoutId = "bullet-list-with-icon-title-description-slide";
export const slideLayoutName = "아이콘·제목·설명 글머리 목록 슬라이드";
export const slideLayoutDescription =
  "상단에 제목이 있고 그 아래에 두 개 열의 글머리 목록이 있는 슬라이드. 각 항목은 작은 원형 아이콘 배지, 같은 줄의 짧은 제목, 그리고 바로 아래의 보조 설명으로 구성됩니다.";

export const Schema = z.object({
  title: z.string().min(3).max(12).default("데이터 분석").meta({
    description: "Slide title shown at the top-left.",
  }),
  itemIcon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().default("pulse icon"),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "pulse icon",
  }).meta({
    description: "Icon shown in each analysis list badge.",
  }),
  items: z
    .array(AnalysisItemSchema)

    .max(6)
    .default([
      { title: "제목 1 제목 1 제목 1 제목 1 제목 1", description: "여기에 항목 설명을 입력하세요. 핵심 내용을 간결하게 정리하세요. 자유롭게" },
      { title: "제목 3 제목 3 제목 3 제목 3 제목 3", description: "여기에 항목 설명을 입력하세요. 핵심 내용을 간결하게 정리하세요. 자유롭게 " },
      { title: "제목 2 제목 2 제목 2 제목 2 제목 2", description: "여기에 항목 설명을 입력하세요. 핵심 내용을 간결하게 정리하세요. 자유롭게 " },
      { title: "제목 4 제목 4 제목 4 제목 4 제목 4", description: "여기에 항목 설명을 입력하세요. 핵심 내용을 간결하게 정리하세요. 자유롭게 " },
      { title: "제목 5 제목 5 제목 5 제목 5 제목 5", description: "여기에 항목 설명을 입력하세요. 핵심 내용을 간결하게 정리하세요. 자유롭게 " },
      { title: "제목 6 제목 6 제목 6 제목 6 제목 6", description: "여기에 항목 설명을 입력하세요. 핵심 내용을 간결하게 정리하세요. 자유롭게 " },
    ])
    .meta({
      description: "List of points contains a title and description.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const DataAnalysisListSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, itemIcon, items } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,200..900;1,200..900&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#f9f8f8]"
        style={{
          backgroundColor: "var(--background-color,#f9f8f8)",
          fontFamily: "var(--body-font-family,'Source Sans 3')",
        }}
      >
        <div
          className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
          style={{ height: 185, backgroundColor: "var(--primary-color,#157CFF)" }}
        />

        <div className="px-[58px] pt-[52px]">
          <h2
            className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {title}
          </h2>
        </div>

        <div className="grid grid-cols-2 gap-x-[92px] gap-y-[42px] px-[82px] pt-[58px]">
          {items?.map((item, index) => (
            <div key={`${item.title}-${index}`}>
              <div className="flex items-center gap-[14px]">
                <div
                  className="flex h-[55px] w-[55px] items-center justify-center rounded-full bg-[#157CFF] text-white"
                  style={{
                    backgroundColor: "var(--primary-color,#157CFF)",
                    color: "var(--primary-text,#ffffff)",
                  }}
                >
                  <RemoteSvgIcon
                    url={itemIcon?.__icon_url__}
                    strokeColor={"currentColor"}
                    className="h-[26px] w-[26px] object-contain"
                    color="var(--primary-text, #ffffff)"
                    title={itemIcon?.__icon_query__}
                  />
                  {/* <img
                    src={itemIcon?.__icon_url__}
                    alt={itemIcon?.__icon_query__}
                    className="h-[26px] w-[26px] object-contain"
                    style={{ filter: "brightness(0) invert(1)" }}
                  /> */}
                </div>
                <h3
                  className="text-[20px] font-medium tracking-[2.074px] text-[#232223]"
                  style={{ color: "var(--background-text,#232223)" }}
                >
                  {item.title}
                </h3>
              </div>
              <p
                className="mt-5 max-w-[420px] text-[24px] leading-[26.667px]  text-[#232223]"
                style={{ color: "var(--background-text,#232223)" }}
              >
                {item.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default DataAnalysisListSlide;
