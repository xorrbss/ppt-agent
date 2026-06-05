import * as z from "zod";



export const slideLayoutId = "cover-slide";
export const slideLayoutName = "표지 슬라이드";
export const slideLayoutDescription =
  "왼쪽 상단에 작은 로고, 오른쪽 상단에 날짜/텍스트/라벨, 가운데 정렬된 제목, 그리고 하단에 배치되어 배경으로 부드럽게 사라지는 이미지가 있는 표지 슬라이드.";

export const Schema = z.object({

  label: z.string().min(3).max(16).optional().default("2026년 3월").meta({
    description: "Date/text/label shown at the top-right corner.",
  }),
  titleLine1: z.string().min(3).max(18).default("소셜 미디어").meta({
    description: "First line of the cover title.",
  }),
  titleLine2: z.string().min(3).max(20).default("마케팅 리포트").meta({
    description: "Second line of the cover title.",
  }),
  backgroundImage: z.object({
    __image_url__: z.string().url().default("https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1920&q=80"),
    __image_prompt__: z.string().min(10).max(100).default("Tall glass buildings from street view"),
  }).default({
    __image_url__: "https://images.unsplash.com/photo-1486406146926-c627a92ad1ab?auto=format&fit=crop&w=1920&q=80",
    __image_prompt__: "Tall glass buildings from street view",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CoverSlide = ({ data }: { data: Partial<SchemaType> }) => {


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
        <div
          className="relative z-20 flex h-full flex-col px-[36px] pt-[62px] text-[#15342D]"
          style={{ color: "var(--primary-color,#15342D)" }}
        >
          <div className="flex items-center justify-between">

            <p></p>

            <p
              className="text-[18px] font-normal leading-[18.991px] text-[#15342D]"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {data.label || ''}
            </p>
          </div>

          <div className="flex flex-1 items-center justify-center pb-[80px]">
            <h1
              className="text-center text-[100px] font-semibold leading-[108.4%] tracking-[-3.024px]"
            >
              <p> {data.titleLine1}</p>
              <p>{data.titleLine2} </p>
            </h1>
          </div>
        </div>

        {data.backgroundImage?.__image_url__ && (
          <img
            src={data.backgroundImage.__image_url__ || ''}
            alt={data.backgroundImage.__image_prompt__ || ''}
            className="absolute bottom-0 left-0 z-0 h-[360px] w-full object-cover"
          />
        )}

        <div
          className="pointer-events-none absolute bottom-0 left-0 w-full z-10"
          style={{
            height: "365px",
            background:
              "linear-gradient(0deg, rgba(218, 225, 222, 0.00) 0%, var(--background-color,#DAE1DE) 80.33%)",
          }}
        />
      </div>
    </>
  );
};

export default CoverSlide;
