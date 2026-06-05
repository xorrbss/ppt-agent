import React from 'react'
import * as z from "zod";
import { IconSchema } from '../defaultSchemes';

export const layoutId = 'korean-biz-bullets'
export const layoutName = '핵심 내용'
export const layoutDescription = '제목과, 각각 아이콘·소제목·설명을 담은 3~4개의 글머리 카드로 구성된 한국형 핵심 내용 슬라이드'

// Single source of truth for the sample bullets: used both as the Zod schema
// default and as the component's fallback when no data is provided.
const DEFAULT_BULLETS = [
  {
    icon: { __icon_url__: '', __icon_query__: 'market growth' },
    title: '시장 확대',
    description: '신규 고객 세그먼트를 발굴하고 핵심 채널을 강화해 매출 기반을 넓힙니다.',
  },
  {
    icon: { __icon_url__: '', __icon_query__: 'innovation idea' },
    title: '제품 혁신',
    description: '데이터 기반 의사결정으로 제품 경쟁력을 높이고 차별화를 실현합니다.',
  },
  {
    icon: { __icon_url__: '', __icon_query__: 'team collaboration' },
    title: '조직 역량',
    description: '협업 문화를 정착시키고 인재를 육성하여 실행력을 끌어올립니다.',
  },
];

export const Schema = z.object({
  title: z
    .string()
    .min(3)
    .max(40)
    .default('핵심 전략')
    .meta({ description: "Slide title shown at the top, summarizing the key points" }),
  bullets: z
    .array(
      z.object({
        icon: IconSchema.default({
          __icon_url__: '',
          __icon_query__: 'strategy target',
        }).meta({ description: "Icon representing the key point" }),
        title: z
          .string()
          .min(2)
          .max(30)
          .default('전략')
          .meta({ description: "Short bold heading for the key point" }),
        description: z
          .string()
          .min(10)
          .max(120)
          .default('핵심 전략의 세부 실행 방안을 한 문장으로 설명합니다.')
          .meta({ description: "One or two sentence explanation of the key point" }),
      })
    )
    .min(3)
    .max(4)
    .default(DEFAULT_BULLETS)
    .meta({ description: "List of 3 to 4 key points, each with an icon, heading and description" }),
});

export type BulletPointsData = z.infer<typeof Schema>;

const BulletPointsSlideLayout: React.FC<{ data?: Partial<BulletPointsData> }> = ({ data: slideData }) => {
  const title = slideData?.title || '핵심 전략';
  const bullets = slideData?.bullets && slideData.bullets.length > 0
    ? slideData.bullets
    : DEFAULT_BULLETS;

  return (
    <div
      className="w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video relative z-20 mx-auto overflow-hidden"
      style={{ background: "var(--background-color,#ffffff)", fontFamily: "var(--heading-font-family,'Noto Sans KR')" }}
    >
      <>
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet" />

        <div className="w-full h-full flex flex-col px-20 py-16">
          {/* Title */}
          <div className="flex items-center gap-4 mb-12">
            <div
              className="w-2 h-12 rounded-full"
              style={{ background: "var(--primary-color,#2563eb)" }}
            />
            <h1
              className="text-4xl font-black tracking-tight"
              style={{ color: "var(--background-text,#1a1a2e)" }}
            >
              {title}
            </h1>
          </div>

          {/* Bullet cards */}
          <div
            className={`flex-1 grid gap-6 ${bullets.length >= 4 ? 'grid-cols-4' : 'grid-cols-3'}`}
          >
            {bullets.map((b, i) => (
              <div
                key={i}
                className="flex flex-col rounded-2xl border p-7 h-full"
                style={{ backgroundColor: "var(--card-color,#ffffff)", borderColor: "var(--stroke,#e5e7eb)" }}
              >
                {/* Icon badge */}
                <div
                  className="w-14 h-14 rounded-xl flex items-center justify-center mb-6"
                  style={{ background: "color-mix(in srgb, var(--primary-color,#2563eb) 14%, transparent)" }}
                >
                  {b?.icon?.__icon_url__ ? (
                    <img
                      src={b.icon.__icon_url__}
                      alt={b?.title || '아이콘'}
                      className="w-7 h-7 object-contain"
                    />
                  ) : (
                    <div
                      className="w-7 h-7 rounded-md"
                      style={{ background: "var(--primary-color,#2563eb)" }}
                    />
                  )}
                </div>

                {/* Heading */}
                <h2
                  className="text-xl font-bold mb-3"
                  style={{ color: "var(--background-text,#1a1a2e)" }}
                >
                  {b?.title || '전략'}
                </h2>

                {/* Description */}
                <p
                  className="text-sm leading-relaxed opacity-80"
                  style={{ color: "var(--background-text,#1a1a2e)" }}
                >
                  {b?.description || '설명'}
                </p>
              </div>
            ))}
          </div>
        </div>
      </>
    </div>
  );
};

export default BulletPointsSlideLayout;
