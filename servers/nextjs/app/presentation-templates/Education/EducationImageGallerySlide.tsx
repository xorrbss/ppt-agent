import * as z from "zod";


export const slideLayoutId = "image-gallery-slide";
export const slideLayoutName = "이미지 갤러리 슬라이드";
export const slideLayoutDescription =
  "왼쪽 이미지 갤러리와 갤러리 제목 및 설명을 담은 오른쪽 텍스트 블록으로 구성된 슬라이드.";

export const Schema = z.object({
  title: z.string().max(24).default("이미지 갤러리").meta({
    description: "Heading on the right side.",
  }),
  body: z.string().max(300).default(
    "생생한 현장의 모습을 담은 갤러리를 통해 저희의 교육 활동과 분위기를 한눈에 만나 보세요. 다양한 수업 현장과 학습자들의 열정이 어우러진 순간들을 모았습니다. 사진 속 이야기처럼, 여러분도 이곳에서 새로운 배움의 경험을 시작할 수 있습니다."
  ).meta({
    description: "Supporting paragraph shown below the heading.",
  }),
  galleryImages: z.array(z.object({
    __image_url__: z.string(),
    __image_prompt__: z.string(),
  })).max(5).min(5).default(Array(5).fill({
    __image_url__: "https://images.unsplash.com/photo-1521737604893-d14cc237f11d?auto=format&fit=crop&w=1200&q=80",
    __image_prompt__: "Office team collaboration",
  })).meta({
    description: "Image gallery images.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const EducationImageGallerySlide = ({ data }: { data: Partial<SchemaType> }) => {

  const { title, body, galleryImages } = data;

  return (
    <>

      <link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,200..900;1,8..60,200..900&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden"
        style={{
          backgroundColor: "var(--background-color,#E6E7E8)",
          fontFamily: "var(--body-font-family,'Source Serif 4')",
        }}
      >
        <div className="grid h-full items-end grid-cols-[590px_1fr]">
          <div className="grid h-full grid-cols-2 grid-rows-[245px_245px_230px] gap-[2px]">
            <img
              src={galleryImages?.[0].__image_url__}
              alt={galleryImages?.[0].__image_prompt__}
              className="h-full w-full object-cover object-left"
            />
            <img
              src={galleryImages?.[1].__image_url__}
              alt={galleryImages?.[1].__image_prompt__}
              className="h-full w-full object-cover object-right"
            />
            <img
              src={galleryImages?.[2].__image_url__}
              alt={galleryImages?.[2].__image_prompt__}
              className="h-full w-full object-cover object-top"
            />
            <img
              src={galleryImages?.[3].__image_url__}
              alt={galleryImages?.[3].__image_prompt__}
              className="h-full w-full object-cover object-center"
            />
            <img
              src={galleryImages?.[4].__image_url__}
              alt={galleryImages?.[4].__image_prompt__}
              className="col-span-2 h-full w-full object-cover object-bottom"
            />
          </div>

          <div className="px-[64px] pb-[56px] ">
            <h2 className="font-serif text-[64px] font-medium leading-[98%]" style={{ color: "var(--primary-color,#101C3D)" }}>
              {title}
            </h2>
            <p className="mt-[37px] text-[22px]" style={{ color: "var(--background-text,#34394C)" }}>
              {body}
            </p>
          </div>
        </div>
      </div>
    </>
  );
};

export default EducationImageGallerySlide;
