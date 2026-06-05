import React from "react";
import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";
import * as z from "zod";
import { IconSchema } from "../defaultSchemes";

export const layoutId = "bullet-with-icons-description-grid";
export const layoutName = "아이콘 글머리 설명 그리드";
export const layoutDescription =
  "아이콘 글머리 설명 그리드 슬라이드 레이아웃";

const bulletWithIconsDescriptionGridSlideSchema = z.object({
  title: z.string().min(3).max(25).default("기업의 어려움").meta({
    description: "Main title of the slide",
  }),
  mainDescription: z
    .string()
    .min(20)
    .max(300)
    .default(
      "앞서 설명하고 파악한 문제들을 해결하는 솔루션을 제시합니다. 우리가 제공하는 솔루션이 효과성과 효율성의 가치를 지키며 시장 상황과 사회에 매우 적합하다는 점을 분명히 보여줍니다."
    )
    .meta({
      description: "Main content text describing the solution",
    }),
  sections: z
    .array(
      z.object({
        title: z.string().min(3).max(30).meta({
          description: "Section title",
        }),
        description: z.string().min(5).max(70).meta({
          description: "Section description",
        }),
        icon: IconSchema.optional().meta({
          description: "Icon for the section",
        }),
      })
    )
    .min(2)
    .max(6)
    .default([
      {
        title: "시장",
        description:
          "혁신적이고 폭넓게 인정받습니다. 혁신적이고 폭넓게 인정받습니다. 혁신적이고 폭넓게 인정받습니다.",
        icon: {
          __icon_query__: "market innovation",
          __icon_url__:
            "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/checks-bold.svg",
        },
      },
      {
        title: "산업",
        description: "건전한 시장 판단을 기반으로 합니다.",
        icon: {
          __icon_query__: "industry building",
          __icon_url__:
            "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/fediverse-logo-bold.svg",
        },
      },
      {
        title: "SEM",
        description: "정밀한 데이터와 분석을 기반으로 합니다.",
        icon: {
          __icon_query__: "SEM data analysis",
          __icon_url__:
            "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/video-bold.svg",
        },
      },
      {
        title: "최종 사용자",
        description: "실제 사용자 영향에 집중합니다.",
        icon: {
          __icon_query__: "end user impact",
          __icon_url__:
            "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/users-four-bold.svg",
        },
      },
      {
        title: "산업",
        description: "건전한 시장 판단을 기반으로 합니다.",
        icon: {
          __icon_query__: "industry building",
          __icon_url__:
            "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/fediverse-logo-bold.svg",
        },
      },
      {
        title: "SEM",
        description: "정밀한 데이터와 분석을 기반으로 합니다.",
        icon: {
          __icon_query__: "SEM data analysis",
          __icon_url__:
            "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/bold/video-bold.svg",
        },
      },
    ])
    .meta({
      description:
        "List of solution sections with titles, descriptions, and optional icons",
    }),
});

export const Schema = bulletWithIconsDescriptionGridSlideSchema;

export type BulletWithIconsDescriptionGridSlideData = z.infer<
  typeof bulletWithIconsDescriptionGridSlideSchema
>;

interface BulletWithIconsDescriptionGridSlideLayoutProps {
  data?: Partial<BulletWithIconsDescriptionGridSlideData>;
}

const BulletWithIconsDescriptionGridSlideLayout = ({
  data: slideData,
}: BulletWithIconsDescriptionGridSlideLayoutProps) => {
  const sections = slideData?.sections || [];
  return (
    <>
      {/* Import Google Fonts */}
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
        {((slideData as any)?.__companyName__ ||
          (slideData as any)?._logo_url__) && (
          <div className="absolute top-0 left-0 right-0 px-8 sm:px-12 lg:px-20 pt-4">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1">
                {(slideData as any)?._logo_url__ && (
                  <img
                    src={(slideData as any)?._logo_url__}
                    alt="logo"
                    className="w-6 h-6"
                  />
                )}
                {(slideData as any)?.__companyName__ && (
                  <span
                    className="text-sm sm:text-base font-semibold"
                    style={{ color: "var(--background-text, #111827)" }}
                  >
                    {(slideData as any)?.__companyName__ || "회사명"}
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Main Content */}
        <div className="flex justify-center items-center h-full px-16 pb-16 gap-4">
          {/* Title and Description */}
          <div className="w-full flex flex-col items-start mb-4">
            <h1
              className="text-5xl font-bold mb-8 leading-tight text-left"
              style={{ color: "var(--background-text, #1E4CD9)" }}
            >
              {slideData?.title}
            </h1>
            <p
              className="text-lg leading-relaxed font-normal mb-12 max-w-lg text-left"
              style={{ color: "var(--background-text, #334155)" }}
            >
              {slideData?.mainDescription}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4 w-full">
            {sections.map((section, idx) => (
              <div
                key={idx}
                className="flex flex-col items-center text-center rounded-lg shadow px-3 py-4 "
                style={{ backgroundColor: "var(--card-color, #F5F8FE)" }}
              >
                <div className="mb-2">
                  {section?.icon?.__icon_url__ && (
                    <RemoteSvgIcon
                      url={section.icon.__icon_url__}
                      strokeColor={"currentColor"}
                      className="w-12 h-12 mb-2"
                      color="var(--primary-color, #1E4CD9)"
                      title={section.icon.__icon_query__}
                    />
                  )}
                </div>
                <h2
                  className="text-lg font-semibold mb-1"
                  style={{ color: "var(--background-text, #234CD9)" }}
                >
                  {section.title}
                </h2>
                <div
                  className="w-8 h-1 mb-2"
                  style={{ backgroundColor: "var(--primary-color, #234CD9)" }}
                ></div>
                <p
                  className="text-xs leading-snug"
                  style={{ color: "var(--background-text, #234CD9)" }}
                >
                  {section.description}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom Border */}
        <div
          className="absolute bottom-0 left-0 right-0 h-1"
          style={{ backgroundColor: "var(--primary-color, #1E4CD9)" }}
        ></div>
      </div>
    </>
  );
};

export default BulletWithIconsDescriptionGridSlideLayout;
