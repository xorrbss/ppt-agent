import React from "react";
import * as z from "zod";
import { ImageSchema } from "../defaultSchemes";

export const layoutId = "metrics-with-description-image";
export const layoutName = "설명과 이미지가 있는 지표 슬라이드 레이아웃";
export const layoutDescription =
  "슬라이드 전체에 이미지를 사용하는, 설명이 있는 지표 슬라이드 레이아웃";

const marketSizeSlideSchema = z.object({
  title: z.string().min(3).max(15).default("시장 규모").meta({
    description: "Main slide title",
  }),


  mapImage: ImageSchema.default({
    __image_url__:
      "https://upload.wikimedia.org/wikipedia/commons/8/80/World_map_-_low_resolution.svg", // You can quickly find a world map image via a Google search or use a free resource like Wikimedia Commons
    __image_prompt__: "World map with location pins or points",
  }),
  marketStats: z
    .array(
      z.object({
        label: z.string().min(3).max(30),
        value: z.string().min(3).max(30),
        description: z.string().min(3).max(130),
      }),
    )
    .min(1)
    .max(4)
    .default([
      {
        label: "전체 시장 규모(TAM)",
        value: "14억",
        description:
          "TAM 섹션에서는 제품을 구매할 수 있는 모든 사람의 잠재력 또는 기업이 제품 판매로 얻을 수 있는 최대 매출을 기재할 수 있습니다.",
      },
      {
        label: "유효 시장 규모(SAM)",
        value: "1억 9,400만",
        description:
          "제품 유형, 보유 기술, 지리적 조건을 고려할 때 기업의 목표 시장이 될 잠재력을 가진 TAM의 일부입니다.",
      },
      {
        label: "전체 시장 규모(TAM)",
        value: "14억",
        description:
          "TAM 섹션에서는 제품을 구매할 수 있는 모든 사람의 잠재력 또는 기업이 제품 판매로 얻을 수 있는 최대 매출을 기재할 수 있습니다.",
      },
      {
        label: "유효 시장 규모(SAM)",
        value: "1억 9,400만",
        description:
          "제품 유형, 보유 기술, 지리적 조건을 고려할 때 기업의 목표 시장이 될 잠재력을 가진 TAM의 일부입니다.",
      }
    ])
    .meta({
      description:
        "Market statistics including TAM, SAM, and SOM with labels, values, and descriptions.",
    }),
  description: z
    .string()
    .default(
      "시장 규모는 이해관계자가 직접 확인할 수 있는 모든 매출과 고객의 총량입니다. 이 기법은 보통 연말에 산출되며, 기업은 시장 규모를 활용해 향후 자사의 시장과 사업의 잠재력을 가늠할 수 있습니다. 이는 특히 우리 서비스에 관심 있는 이들에게 서비스를 제공하려는 신생 기업에 매우 유용합니다.",
    )
    .meta({
      description: "Main description text for the slide",
    }),
});

export const Schema = marketSizeSlideSchema;
export type MarketSizeSlideData = z.infer<typeof marketSizeSlideSchema>;

interface MarketSizeSlideProps {
  data?: Partial<MarketSizeSlideData>;
}

const MarketSizeSlideLayout: React.FC<MarketSizeSlideProps> = ({
  data: slideData,
}) => {
  const stats = slideData?.marketStats || [];

  return (
    <>
      {/* Montserrat Font */}
      <link
        href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
        rel="stylesheet"
      />

      <div
        className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
        style={{
          fontFamily: "var(--heading-font-family,Montserrat)",
          backgroundColor: 'var(--background-color, #FFFFFF)'
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

        {/* Main Content */}
        <div className="flex h-full px-16 pb-16">
          {/* Title and Map on the left */}
          <div className="flex flex-col items-center justify-center w-[48%] pr-8 h-full">
            <div className="flex flex-col items-left justify-center h-full w-full">
              {/* Move the title down to align with the top of the market stats */}
              <h1
                className="text-5xl font-bold mb-8 leading-tight text-left"
                style={{ color: 'var(--background-text, #1E4CD9)' }}>
                {slideData?.title || "시장 규모"}
              </h1>
              <div className="w-full bg-[#CBE3CC] rounded-md mb-8 flex items-center justify-center">
                {slideData?.mapImage?.__image_url__ && (
                  <img
                    src={slideData?.mapImage?.__image_url__}
                    alt="지점이 표시된 세계 시장 지도"
                    className="w-full object-contain rounded-md"
                    style={{ maxHeight: 220 }}
                  />
                )}
              </div>
              {slideData?.description && (
                <p className="text-sm leading-relaxed font-normal mb-12 max-w-lg text-left" style={{ color: 'var(--background-text, #234CD9)' }}>
                  {slideData?.description}
                </p>
              )}
            </div>
          </div>

          {/* Market Stats on the right - vertically centered */}
          <div className="flex flex-col items-start justify-center w-[52%] gap-8">
            <div className="w-full space-y-10">
              {stats.map((stat, index) => (
                <div key={index}>
                  <div className="space-y-2">
                    <div className="text-white text-sm font-semibold px-3 py-1 inline-block rounded-sm" style={{ backgroundColor: 'var(--primary-color, #234CD9)', color: 'var(--primary-text, #ffffff)' }}>
                      <span className="text-sm">{stat.label}</span>
                    </div>
                    <div className="text-2xl font-bold" style={{ color: 'var(--primary-color, #1E4CD9)' }}>
                      {stat.value}
                    </div>
                  </div>
                  <p className="text-sm leading-snug" style={{ color: 'var(--background-text, #334155)' }}>
                    {stat.description}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

export default MarketSizeSlideLayout;
