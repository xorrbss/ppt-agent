import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";
import * as z from "zod";

export const slideLayoutId = "horizontal-timeline";
export const slideLayoutName = "가로 타임라인";
export const slideLayoutDescription =
  "단계 마커, 항목 텍스트, 이어짐 상태, 선택적 종료 라벨을 갖춘 가로 타임라인입니다.";

const MAX_TIMELINE_ITEMS_PER_SLIDE = 5;
const DEFAULT_ICON = {
  __icon_url__:
    "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
  __icon_query__: "shield check icon",
};

const TimelineItemSchema = z.object({
  label: z.string().max(10).meta({
    description: "Short label above marker icon.",
  }),
  icon: z
    .object({
      __icon_url__: z.string(),
      __icon_query__: z.string(),
    })
    .default(DEFAULT_ICON),
  title: z.string().max(16).meta({
    description: "Heading below marker icon.",
  }),
  description: z.string().max(132).meta({
    description: "Supporting copy for each timeline item.",
  }),
});

const DEFAULT_DESCRIPTION =
  "여기에 청중에게 전달할 핵심 메시지를 입력하세요. 이 영역에는 보조 설명과 세부 내용을 자유롭게 작성할 수 있습니다.";

export const Schema = z.object({
  title: z.string().max(18).default("타임라인").meta({
    description: "Top-left heading.",
  }),
  isContinue: z.boolean().default(false).meta({
    description:
      "Whether this slide continues a previous timeline slide. Continuation slides use the Continue... heading and draw the axis in from the left edge.",
  }),
  showEndLabel: z.boolean().default(true).meta({
    description: "Whether to show right-end label near timeline axis.",
  }),
  endLabel: z.string().max(12).default("끝").meta({
    description: "Right-end label text.",
  }),
  items: z
    .array(TimelineItemSchema)

    .max(MAX_TIMELINE_ITEMS_PER_SLIDE)
    .default([
      {
        label: "1단계",
        icon: DEFAULT_ICON,
        title: "텍스트 입력",
        description: DEFAULT_DESCRIPTION,
      },
      {
        label: "2단계",
        icon: DEFAULT_ICON,
        title: "텍스트 입력",
        description: DEFAULT_DESCRIPTION,
      },
      {
        label: "3단계",
        icon: DEFAULT_ICON,
        title: "텍스트 입력",
        description: DEFAULT_DESCRIPTION,
      },
      {
        label: "4단계",
        icon: DEFAULT_ICON,
        title: "텍스트 입력",
        description: DEFAULT_DESCRIPTION,
      },
      {
        label: "5단계",
        icon: DEFAULT_ICON,
        title: "텍스트 입력",
        description: DEFAULT_DESCRIPTION,
      },
    ])
    .meta({
      description: "Timeline items from left to right.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const SLIDE_WIDTH = 1280;
const TIMELINE_AXIS_Y = 355;
const TIMELINE_LEFT_CENTER = 106;
const TIMELINE_RIGHT_CENTER = 1058;
const THREE_STEP_CENTERS = [106, 580, 1058];
const CONTINUATION_TITLE = "계속...";

const getTimelineCenters = (count: number) => {
  if (count === 3) {
    return THREE_STEP_CENTERS;
  }

  if (count <= 1) {
    return [TIMELINE_LEFT_CENTER];
  }

  return Array.from({ length: count }, (_, index) => {
    return (
      TIMELINE_LEFT_CENTER +
      ((TIMELINE_RIGHT_CENTER - TIMELINE_LEFT_CENTER) * index) / (count - 1)
    );
  });
};

const getTimelineStyle = (count: number) => {
  if (count <= 3) {
    return {
      badgeSize: 88,
      labelTop: 268,
      titleTop: 423,
      contentWidth: 260,
      labelFontSize: 25,
      titleFontSize: 25,
      bodyFontSize: 17,
    };
  }

  if (count === 4) {
    return {
      badgeSize: 76,
      labelTop: 274,
      titleTop: 417,
      contentWidth: 240,
      labelFontSize: 23,
      titleFontSize: 25,
      bodyFontSize: 17,
    };
  }

  return {
    badgeSize: 66,
    labelTop: 280,
    titleTop: 412,
    contentWidth: 210,
    labelFontSize: 20,
    titleFontSize: 25,
    bodyFontSize: 17,
  };
};

function TimelineIconBadge({
  icon,
  size,
}: {
  icon: { __icon_url__: string; __icon_query__: string };
  size: number;
}) {
  return (
    <span
      className="inline-flex items-center justify-center rounded-full"
      style={{
        width: size,
        height: size,
        backgroundColor: "var(--primary-color,#dddac7)",
      }}
    >
      {/* <img
        src={icon.__icon_url__}
        alt={icon.__icon_query__}
        className="object-contain"
        style={{ width: size * 0.42, height: size * 0.42 }}
      /> */}
      <RemoteSvgIcon
        url={icon.__icon_url__}
        className={`w-[${size}px] h-[${size}px]`}
        strokeColor={"currentColor"}
        color="var(--primary-text,#27292d)"
        title={icon.__icon_query__}
      />
    </span>
  );
}

const HorizontalTimeline = ({ data }: { data: Partial<SchemaType> }) => {
  const slideData = data as SchemaType;
  const timelineItems = slideData.items;
  const timelineCenters = getTimelineCenters(timelineItems.length);
  const timelineStyle = getTimelineStyle(timelineItems.length);
  const firstCenter = timelineCenters[0] ?? TIMELINE_LEFT_CENTER;
  const lastCenter =
    timelineCenters[timelineCenters.length - 1] ?? TIMELINE_RIGHT_CENTER;
  const axisStart = slideData.isContinue ? 0 : firstCenter;
  const axisEnd = slideData.showEndLabel ? lastCenter : SLIDE_WIDTH;
  const endLabelLeft = Math.min(
    SLIDE_WIDTH - 152,
    lastCenter + timelineStyle.badgeSize / 2 + 18
  );

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
        <div className="px-[34px] pt-[40px]">
          <h2
            className="font-serif text-[100px] leading-none"
            style={{
              color: "var(--primary-color,#dddac7)",
              fontFamily: "var(--heading-font-family,'DM Serif Display')",
            }}
          >
            {slideData.title}
          </h2>
        </div>

        <div
          className="absolute h-[2px]"
          style={{
            left: axisStart,
            top: TIMELINE_AXIS_Y,
            width: axisEnd - axisStart,
            backgroundColor: "var(--primary-color,#dddac7)",
          }}
        />

        {timelineItems.map((phase, index) => {
          const centerX = timelineCenters[index] ?? TIMELINE_LEFT_CENTER;
          const textLeft = Math.max(
            30,
            Math.min(
              SLIDE_WIDTH - timelineStyle.contentWidth - 30,
              centerX - timelineStyle.badgeSize / 2
            )
          );

          return (
            <div key={`${phase.label}-${index}`}>
              <p
                className="absolute leading-none"
                style={{
                  left: textLeft,
                  top: timelineStyle.labelTop,
                  color: "var(--primary-text,#d7d3be)",
                  fontSize: timelineStyle.labelFontSize,
                }}
              >
                {phase.label}
              </p>

              <div
                className="absolute"
                style={{
                  left: centerX - timelineStyle.badgeSize / 2,
                  top: TIMELINE_AXIS_Y - timelineStyle.badgeSize / 2,
                }}
              >
                <TimelineIconBadge
                  icon={phase.icon}
                  size={timelineStyle.badgeSize}
                />
              </div>

              <div
                className="absolute"
                style={{
                  left: textLeft,
                  top: timelineStyle.titleTop,
                  width: timelineStyle.contentWidth,
                }}
              >
                <p
                  className="leading-none"
                  style={{
                    color: "var(--background-text,#d7d3be)",
                    fontSize: timelineStyle.titleFontSize,
                  }}
                >
                  {phase.title}
                </p>

                <p
                  className="mt-[12px] leading-[1.16]"
                  style={{
                    color: "var(--background-text,#cbc7b2)",
                    fontSize: timelineStyle.bodyFontSize,
                  }}
                >
                  {phase.description}
                </p>
              </div>
            </div>
          );
        })}

        {slideData.showEndLabel && (
          <p
            className="absolute top-[346px] text-[25px] leading-none"
            style={{
              left: endLabelLeft,
              color: "var(--primary-text,#d7d3be)",
            }}
          >
            {slideData.endLabel}
          </p>
        )}
      </div>
    </>
  );
};

export default HorizontalTimeline;
