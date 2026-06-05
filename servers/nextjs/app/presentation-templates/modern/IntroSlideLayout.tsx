import React from "react";
import * as z from "zod";
import { ImageSchema } from "../defaultSchemes";

export const layoutId = "intro-pitchdeck-slide";
export const layoutName = "피치덱 소개 슬라이드";
export const layoutDescription =
  "큰 제목, 회사명, 날짜, 연락처 정보를 모던한 디자인으로 담은, 시각적으로 매력적인 피치덱 소개 슬라이드입니다. 이 슬라이드는 항상 피치덱의 첫 번째 슬라이드로, 깔끔하고 전문적인 느낌으로 발표의 분위기를 잡아줍니다.";
const introPitchDeckSchema = z.object({
  title: z.string().min(2).max(15).default("피치덱").meta({
    description: "Main title of the slide",
  }),
  description: z
    .string()
    .min(1)
    .max(200)
    .default("여기에 짧은 부제목이나 설명을 추가하세요. 여기에 짧은 부제목이나 설명을 추가하세요. 여기에 짧은 부제목이나 설명을 추가하세요. 여기에 짧은 부제목이나 설명을 추가하세요.")
    .meta({
      description: "Description shown below the title",
    }),
  introCard: z
    .object({
      enabled: z.boolean().default(true),
      name: z.string().min(1).max(60).default("홍길동"),
      date: z.string().min(1).max(60).default("2025년 12월"),
    })
    .default({ enabled: true, name: "홍길동", date: "2025년 12월" })
    .meta({ description: "Optional intro card shown below description" }),
  image: ImageSchema.default({
    __image_url__:
      "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?q=80&w=1600&auto=format&fit=crop",
    __image_prompt__: "Abstract business background",
  }),


});

export const Schema = introPitchDeckSchema;
export type IntroPitchDeckData = z.infer<typeof introPitchDeckSchema>;

interface IntroSlideLayoutProps {
  data: Partial<IntroPitchDeckData>;
}

const IntroPitchDeckSlide: React.FC<IntroSlideLayoutProps> = ({
  data: slideData,
}) => {

  return (
    <>
      {/* Montserrat Font */}
      <link
        href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
        rel="stylesheet"
      />
      <div
        className="w-full max-w-[1280px] aspect-video mx-auto relative overflow-hidden rounded-md"
        style={{
          fontFamily: "var(--heading-font-family,Montserrat)",
          backgroundColor: 'var(--background-color, #FFFFFF)',
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      >
        {/* Top Header */}
        {((slideData as any)?.__companyName__ || (slideData as any)?._logo_url__) && (
          <div className="absolute top-0 left-0 right-0 px-8 sm:px-12 lg:px-20 pt-4">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1">

                {(slideData as any)?._logo_url__ && <img src={(slideData as any)?._logo_url__} alt="logo" className="w-6 h-6" />}
                {(slideData as any)?.__companyName__ && <span className="text-sm sm:text-base font-semibold" style={{ color: 'var(--background-text, #111827)' }}>
                  {(slideData as any)?.__companyName__ || '회사명'}
                </span>}
              </div>
            </div>
          </div>
        )}

        {/* Main Title and Description (shifted slightly up) */}
        <div
          className="absolute left-10 right-[42%]"
          style={{
            top: "50%",
            transform: "translateY(-50%)",
          }}
        >
          {slideData?.title && (
            <div className="relative inline-block">
              <h1
                className="text-5xl font-bold leading-none"
                style={{ color: 'var(--background-text, #1E4CD9)' }}
                id="pitchdeck-title"
              >
                {slideData?.title}
              </h1>
              <span
                className="block h-[4px] absolute left-0"
                style={{
                  width: "50%",
                  bottom: "-0.5em",
                  transition: "width 0.3s",
                  backgroundColor: 'var(--primary-color, #1E4CD9)'
                }}
              />
            </div>
          )}
          <p className="text-lg leading-relaxed font-normal mt-6 max-w-xl" style={{ color: 'var(--background-text, #234CD9)' }}>
            {slideData?.description}
          </p>
          {slideData?.introCard?.enabled && (
            <div className="mt-6 inline-flex items-center gap-4 rounded-lg px-5 py-4 shadow-sm min-w-[400px]" style={{ backgroundColor: 'var(--card-color, #FFFFFF)', border: '1px solid var(--stroke, #E5E7EB)' }}>
              <div className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold" style={{ backgroundColor: 'var(--primary-color, #F5F8FE)', color: 'var(--primary-text, #234CD9)' }}>
                {(slideData?.introCard?.name || "").split(" ").map(p => p.charAt(0)).join("").slice(0, 2).toUpperCase()}
              </div>
              <div className="flex flex-col">
                <div className="text-[16px] font-semibold" style={{ color: 'var(--background-text, #234CD9)' }}>{slideData?.introCard?.name}</div>
                <div className="text-[14px]" style={{ color: 'var(--background-text, #234CD9)' }}>{slideData?.introCard?.date}</div>
              </div>
            </div>
          )}
        </div>

        {/* Right Image */}
        {slideData?.image && slideData?.image?.__image_url__ && (
          <div className="absolute top-16 bottom-16 right-10 w-[42%] rounded-md overflow-hidden">
            <img
              src={slideData?.image?.__image_url__}
              alt={slideData?.image?.__image_prompt__ || slideData?.title || "intro-image"}
              className="w-full h-full object-cover"
              crossOrigin="anonymous"
              referrerPolicy="no-referrer"
            />
          </div>
        )}
      </div>
    </>
  );
};

export default IntroPitchDeckSlide;
