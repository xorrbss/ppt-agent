import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";
import * as z from "zod";

const FeatureCardSchema = z.object({
  title: z.string().min(3).max(17).meta({
    description: "Title shown on each card.",
  }),
  description: z.string().min(18).max(80).meta({
    description: "Description shown on each card.",
  }),
  icon: z.object({
    __icon_url__: z.string().meta({
      description: "URL to icon",
    }),
    __icon_query__: z.string().meta({
      description: "Query used to search the icon",
    }),
  }).default({
    __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "check icon",
  }).meta({
    description: "Suiting icon used for each bullet in plan cards.",
  }),
});

export const slideLayoutId = "cards-grid-slide";
export const slideLayoutName = "카드 그리드 슬라이드";
export const slideLayoutDescription =
  "각 카드에 제목, 아이콘, 간단한 설명이 담긴 카드 그리드 목록.";

export const Schema = z.object({
  title: z.string().min(6).max(20).default("기능 그리드").meta({
    description: "Slide title shown above the grid.",
  }),
  features: z
    .array(FeatureCardSchema)
    .min(1)
    .max(6)
    .default([
      {
        title: "최신 스택",
        description: "최고의 개발자 경험을 위해 React, TypeScript, Tailwind CSS로 구축했습니다.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "컴포넌트 라이브러리 ",
        description: "일관된 디자인 패턴을 갖춘 재사용 가능한 UI 컴포넌트.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "CLI 도구",
        description: "스캐폴딩과 자동화를 위한 커맨드라인 유틸리티.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "분석",
        description: "내장된 추적 및 성능 모니터링.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "버전 관리",
        description: "자동 배포를 갖춘 Git 기반 워크플로.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "모범 사례",
        description: "업계 표준과 최신 개발 패턴을 따릅니다.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
    ])
    .meta({
      description: "Six feature cards displayed in a 3x2 grid.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide04FeatureGrid = ({ data }: { data: Partial<SchemaType> }) => {


  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:ital,opsz,wght@0,6..12,200..1000;1,6..12,200..1000&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden p-[53px]"
        style={{
          backgroundColor: "var(--background-color,#101B37)",
          fontFamily: "var(--body-font-family,Nunito Sans)",
        }}
      >


        <h2 className="text-[64px] font-medium tracking-[-0.03em]" style={{ color: "var(--background-text,#f2f4ff)" }}>{data.title}</h2>

        <div className="mt-[26px] grid flex-1 grid-cols-3 items-center h-fit  gap-[26px]">
          {data?.features?.map((feature) => (
            <div
              key={feature.title}
              className="rounded-[18px] border p-[26px]"
              style={{
                boxShadow: "0 33.333px 66.667px -16px rgba(0, 0, 0, 0.25)",
                borderColor: "var(--stroke,#1D293D80)",
                backgroundColor: "var(--card-color,#0F172B80)",
              }}
            >
              <div className="flex items-start justify-between gap-[8px]">
                <h3 className="text-[26px] font-medium" style={{ color: "var(--background-text,#ffffff)" }}>{feature.title}</h3>
                <span
                  className="flex h-[52px] w-[52px] items-center justify-center rounded-full border text-[18px]"
                  style={{
                    borderColor: "var(--primary-color,#2B7FFF4D)",
                    backgroundColor: "var(--primary-color,#2B7FFF33)",
                  }}
                >
                  {/* <img src={feature.icon.__icon_url__} alt={feature.icon.__icon_query__} className="h-[24px] w-[24px] object-contain"
                    style={{
                      filter: "invert(1)",
                    }}
                  /> */}
                  <RemoteSvgIcon
                    url={feature.icon?.__icon_url__}
                    strokeColor={"currentColor"}
                    className="h-[24px] w-[24px] object-contain"
                    color="var(--primary-text, #ffffff)"
                    title={feature.icon.__icon_query__}
                  />
                </span>
              </div>
              <p className="mt-[12px] text-[18px] leading-[136%]" style={{ color: "var(--background-text,#90A1B9)" }}>{feature.description}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default CodeSlide04FeatureGrid;
