import * as z from "zod";

export const slideLayoutId = "media-and-text-split";
export const slideLayoutName = "미디어와 텍스트 분할";
export const slideLayoutDescription =
  "왼쪽에 제목과 미디어 블록, 오른쪽에 보조 설명과 푸터 텍스트를 배치한 분할 구성입니다.";

export const Schema = z
  .object({
    title: z
      .string()
      .max(16)
      .meta({
        description: "Left panel heading.",
      })
      .default("개요"),
    sidePanelMode: z.enum(["solid", "image"]).default("image").meta({
      description: "Left media panel mode.",
    }),
    sidePanelColor: z.string().max(20).default("#d3d0bc").meta({
      description: "Left media color used in solid mode.",
    }),
    sidePanelImage: z
      .object({
        __image_url__: z
          .string()
          .default(
            "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1400&q=80"
          ),
        __image_prompt__: z.string().default("Glass skyscraper perspective"),
      })
      .meta({
        description: "Left media image used in image mode.",
      }),
    headline: z
      .string()

      .max(50)
      .default(
        "여기에 청중에게 전달할 이야기를 담은 예시 문구를 입력하세요"
      )
      .meta({
        description: "Main headline text on the right.",
      }),
    body: z
      .string()

      .max(128)
      .default(
        "여기에 청중에게 전달할 핵심 메시지를 입력하세요. 이 영역에는 보조 설명과 세부 내용을 자유롭게 작성할 수 있습니다."
      )
      .meta({
        description: "Supporting paragraph text.",
      }),
    footerText: z.string().max(28).default("푸터 텍스트").meta({
      description: "Footer text at the bottom-right.",
    }),
  })
  .default({
    title: "개요",
    sidePanelMode: "image",
    sidePanelColor: "#d3d0bc",
    sidePanelImage: {
      __image_url__:
        "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1400&q=80",
      __image_prompt__: "Glass skyscraper perspective",
    },
    headline:
      "여기에 청중에게 전달할 이야기를 담은 예시 문구를 입력하세요",
    body: "여기에 청중에게 전달할 핵심 메시지를 입력하세요. 이 영역에는 보조 설명과 세부 내용을 자유롭게 작성할 수 있습니다.",
    footerText: "푸터 텍스트",
  });

export type SchemaType = z.infer<typeof Schema>;

const MediaAndTextSplit = ({ data }: { data: Partial<SchemaType> }) => {
  const slideData = data as SchemaType;

  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&display=swap"
        rel="stylesheet"
      />

      <div
        className="relative h-[720px] w-[1280px] overflow-hidden "
        style={{
          backgroundColor: "var(--background-color,#27292d)",
          fontFamily: "var(--body-font-family,'DM Serif Display')",
        }}
      >
        <h2
          className="px-[38px] pt-[48px]  text-[100px] leading-none tracking-[-0.02em]"
          style={{
            color: "var(--background-text,#dddac7)",
          }}
        >
          {slideData.title}
        </h2>
        <div className="flex   items-center mt-[30px]">
          <div
            className=" w-[572px] h-[542px]"
            style={{
              backgroundColor:
                slideData.sidePanelMode === "solid"
                  ? slideData.sidePanelColor
                  : "transparent",
            }}
          >
            {slideData.sidePanelMode === "image" && (
              <img
                src={slideData.sidePanelImage.__image_url__}
                alt={slideData.sidePanelImage.__image_prompt__}
                className="h-full w-full object-cover"
              />
            )}
          </div>
          <div className="px-[66px]  flex-1 mt-[31px] flex flex-col  h-full">
            <div className="flex-1">
              <h3
                className="max-w-[610px] text-[32px] leading-[1.08]"
                style={{ color: "var(--background-text,#d7d3be)" }}
              >
                {slideData.headline}
              </h3>
              <p
                className="mt-[34px] max-w-[610px] text-[22px] leading-[1.16]"
                style={{ color: "var(--background-text,#cbc7b2)" }}
              >
                {slideData.body}
              </p>
            </div>
            <p
              className="mt-[100px] text-[34px] leading-none"
              style={{ color: "var(--background-text,#dddac7)" }}
            >
              {slideData.footerText}
            </p>
          </div>
        </div>
      </div>
    </>
  );
};

export default MediaAndTextSplit;
