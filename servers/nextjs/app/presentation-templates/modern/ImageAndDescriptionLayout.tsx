import React from "react";
import * as z from "zod";
import { ImageSchema } from "../defaultSchemes";

export const layoutId = "image-and-description";
export const layoutName = "이미지와 설명";
export const layoutDescription =
  "제목, 설명, 이미지로 구성된 슬라이드 레이아웃.";

const imageWithDescriptionSlideSchema = z.object({
  title: z.string().min(3).max(30).default("이미지와 설명").meta({
    description: "Main title of the slide",
  }),
  content: z
    .string()
    .min(25)
    .max(300)
    .default(
      "발표 세션에서 배경/소개 부분은 발표 도입부의 논의 자료로 활용할 흥미로운 주제를 중심으로 체계적이고 효과적으로 정리된 정보로 채울 수 있습니다. 소개는 청중에게 전반적인 개요를 제공하여, 이 배경/도입 발표 세션 동안 논의 주제의 핵심 키워드가 강조되도록 합니다.",
    )
    .meta({
      description: "Main content text describing the company or topic",
    }),

  image: ImageSchema.default({
    __image_url__:
      "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?q=80&w=1600&auto=format&fit=crop",
    __image_prompt__: "Abstract business background",
  }).meta({
    description:
      "Optional supporting image for the slide (building, office, etc.)",
  }),
});

export const Schema = imageWithDescriptionSlideSchema;

export type ImageWithDescriptionSlideData = z.infer<typeof imageWithDescriptionSlideSchema>;

interface ImageWithDescriptionSlideLayoutProps {
  data?: Partial<ImageWithDescriptionSlideData>;
}

const ImageWithDescriptionSlideLayout: React.FC<ImageWithDescriptionSlideLayoutProps> = ({
  data: slideData,
}) => {
  return (
    <>
      {/* Import fonts */}
      <link
        href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
        rel="stylesheet"
      />

      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg  aspect-video relative z-20 mx-auto overflow-hidden"
        style={{
          fontFamily: "var(--heading-font-family,Montserrat)",
          backgroundColor: "var(--background-color, #FFFFFF)",
        }}
      >
        {/* Header */}
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

        {/* Main content area */}
        <div className="flex h-full px-16 pb-16">
          {/* Left side - Image */}
          <div className="flex-1 pr-16 flex items-center pt-8">
            <div className="w-full h-96 overflow-hidden">
              {slideData?.image ? (
                <img
                  src={slideData.image.__image_url__}
                  alt={slideData.image.__image_prompt__}
                  className="w-full h-full object-cover"
                />
              ) : (
                /* Default building facade */
                <div className="w-full h-full bg-gray-200 relative">
                  {/* Building structure simulation */}
                  <div className="absolute inset-0 bg-gray-300"></div>

                  {/* Horizontal lines (building floors) */}
                  <div className="absolute inset-0">
                    {[...Array(12)].map((_, i) => (
                      <div
                        key={i}
                        className="absolute w-full border-t border-gray-400 opacity-60"
                        style={{ top: `${(i + 1) * 8}%` }}
                      ></div>
                    ))}
                  </div>

                  {/* Vertical lines (building columns) */}
                  <div className="absolute inset-0">
                    {[...Array(6)].map((_, i) => (
                      <div
                        key={i}
                        className="absolute h-full border-l border-gray-400 opacity-40"
                        style={{ left: `${(i + 1) * 16}%` }}
                      ></div>
                    ))}
                  </div>

                  {/* Windows */}
                  <div className="absolute inset-0 grid grid-cols-4 gap-2 p-4">
                    {[...Array(32)].map((_, i) => (
                      <div
                        key={i}
                        className="bg-blue-100 opacity-60 rounded-sm border border-gray-300"
                      ></div>
                    ))}
                  </div>

                  {/* Building edge highlight */}
                  <div className="absolute right-0 top-0 w-1 h-full bg-white opacity-80"></div>
                </div>
              )}
            </div>
          </div>

          {/* Right side - Content */}
          <div className="flex-1 pl-16 flex flex-col justify-center">
            {slideData?.title && (
              <h2 className="text-5xl font-bold mb-12 leading-tight" style={{ color: 'var(--background-text, #1E4CD9)' }}>
                {slideData?.title}
              </h2>
            )}

            {slideData?.content && (
              <div className="text-lg leading-relaxed font-normal max-w-lg" style={{ color: 'var(--background-text, #334155)' }}>
                {slideData?.content}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
};

export default ImageWithDescriptionSlideLayout;
