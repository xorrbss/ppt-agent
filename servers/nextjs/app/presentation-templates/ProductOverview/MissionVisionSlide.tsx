import * as z from "zod";


export const slideLayoutId = "text-blocks-with-image-block-slide";
export const slideLayoutName = "텍스트 블록과 이미지 블록 슬라이드";
export const slideLayoutDescription =
  "왼쪽 상단 블록에 제목, 오른쪽 상단에 텍스트, 왼쪽 하단에 또 다른 텍스트 블록, 오른쪽 하단 블록에 이미지가 있는 슬라이드.";

export const Schema = z.object({
  title: z.string().min(8).max(30).default("미션과 비전").meta({
    description: "Primary heading shown in the top-left tile.",
  }),

  topleftTextBlockLabel: z.string().min(3).max(20).default("미션").meta({
    description: "Mission section label.",
  }),
  topleftTextBlockBody: z.string().min(40).max(90).default(
    "우리의 사명을 간결하게 설명하는 예시 문구입니다. 실제 내용으로 자유롭게 교체해 사용하실 수 있습니다."
  ).meta({
    description: "Mission paragraph content.",
  }),
  bottomleftTextBlockLabel: z.string().min(3).max(20).default("비전").meta({
    description: "Vision section label.",
  }),
  bottomleftTextBlockBody: z.string().min(40).max(90).default(
    "우리의 비전을 간결하게 설명하는 예시 문구입니다. 실제 내용으로 자유롭게 교체해 사용하실 수 있습니다."
  ).meta({
    description: "Vision paragraph content.",
  }),
  image: z.object({
    __image_url__: z.string(),
    __image_prompt__: z.string(),
  }).optional().meta({
    description: "Bottom-right supporting image. Optional.",
  }).optional().default({
    __image_url__: "https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?auto=format&fit=crop&w=1400&q=80",
    __image_prompt__: "Business silhouette at window skyline",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const MissionVisionSlide = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#DAE1DE)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className="grid h-full grid-cols-2 grid-rows-2">
          <div className="px-[74px] pt-[50px]">
            <h2
              className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {data.title}
            </h2>
          </div>

          <div
            className="pl-[60px] pt-[76px]"
            style={{ backgroundColor: "var(--primary-color,#15342D)" }}
          >
            <p
              className="text-[20px] font-semibold tracking-[2.074px] text-white"
              style={{ color: "var(--primary-text,#edf2f1)" }}
            >
              {data.topleftTextBlockLabel}
            </p>
            <p
              className="mt-[26px] text-[28px] font-normal  text-white"
              style={{ color: "var(--primary-text,#edf2f1)" }}
            >
              {data.topleftTextBlockBody}
            </p>
          </div>

          <div
            className="pl-[53px] py-[53px]"
            style={{ backgroundColor: "var(--primary-color,#15342D)" }}
          >
            <p
              className="text-[20px] font-semibold tracking-[2.074px] text-white"
              style={{ color: "var(--primary-text,#edf2f1)" }}
            >
              {data.bottomleftTextBlockLabel}
            </p>
            <p
              className="mt-[24px] text-[28px] font-normal  text-white"
              style={{ color: "var(--primary-text,#edf2f1)" }}
            >
              {data.bottomleftTextBlockBody}
            </p>
          </div>
          <div
            className="h-full w-full overflow-hidden bg-white"
            style={{ backgroundColor: "var(--card-color,#ffffff)" }}
          >
            {data.image?.__image_url__ && (
              <img
                src={data.image.__image_url__}
                alt={data.image.__image_prompt__}
                className="h-full w-full object-cover"
              />
            )}
          </div>
        </div>
      </div>
    </>
  );
};

export default MissionVisionSlide;
