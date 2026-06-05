import * as z from "zod";


export const slideLayoutId = "title-description-with-cards-slide";
export const slideLayoutName = "제목·설명과 카드 슬라이드";
export const slideLayoutDescription =
  "상단에 제목과 소개 텍스트가 있고, 그 아래에 한 카드를 푸터 스타일로 강조할 수 있는 프로필 카드 그리드가 있는 팀 소개 슬라이드.";

const MemberSchema = z.object({
  title: z.string().min(2).max(12).meta({
    description: "Member role or short heading.",
  }),
  name: z.string().min(2).max(16).meta({
    description: "Member name shown in the card footer.",
  }),
  image: z.object({
    __image_url__: z.string().url().default("https://i.pravatar.cc/600?img=12"),
    __image_prompt__: z.string().min(10).max(100).default("Professional male portrait with suit"),
  }).default({
    __image_url__: "https://i.pravatar.cc/600?img=12",
    __image_prompt__: "Professional male portrait with suit",
  }).meta({
    description: "Portrait image for a team member card.",
  }),
  highlighted: z.boolean().default(false).meta({
    description: "Whether this card uses the dark highlight footer.",
  }),
});

export const Schema = z.object({
  title: z.string().max(18).default("팀 소개").meta({
    description: "Main title at the top-left.",
  }),
  taglineLabel: z.string().max(16).default("태그라인").meta({
    description: "Small heading above team description.",
  }),
  taglineBody: z.string().max(80).default(
    "함께 성장하며 최고의 결과를 만들어 가는 우리 팀을 소개하는 자리 표시 문구입니다."
  ).meta({
    description: "Short descriptive paragraph at top-right.",
  }),
  members: z
    .array(MemberSchema)

    .max(4)
    .default([
      {
        title: "CEO",
        name: "김서연",
        image: {
          __image_url__: "https://i.pravatar.cc/600?img=12",
          __image_prompt__: "Professional male portrait with suit",
        },
        highlighted: false,
      },
      {
        title: "제목 2",
        name: "박지훈",
        image: {
          __image_url__: "https://i.pravatar.cc/600?img=13",
          __image_prompt__: "Professional male portrait with tie",
        },
        highlighted: false,
      },
      {
        title: "제목 3",
        name: "최민준",
        image: {
          __image_url__: "https://i.pravatar.cc/600?img=14",
          __image_prompt__: "Professional male portrait smiling",
        },
        highlighted: true,
      },
      {
        title: "제목 2",
        name: "이하나",
        image: {
          __image_url__: "https://i.pravatar.cc/600?img=15",
          __image_prompt__: "Professional male portrait office",
        },
        highlighted: false,
      },
    ])
    .meta({
      description: "Four team member cards shown in one row.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const MeetTeamSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, taglineLabel, taglineBody, members } = data;

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
        <div className="flex items-start justify-between px-[64px] pt-[50px]">
          <h2
            className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {title}
          </h2>

          <div className="w-[520px]">
            <p
              className="text-[20px] font-semibold tracking-[2.074px] text-white"
              style={{ color: "var(--primary-color,#15342D)" }}
            >
              {taglineLabel}
            </p>
            <p
              className="mt-[14px] text-[24px] font-normal  text-[#15342DCC]"
              style={{ color: "var(--background-text,#15342DCC)" }}
            >
              {taglineBody}
            </p>
          </div>
        </div>

        <div className="absolute bottom-[40px]  left-[64px] grid grid-cols-4 gap-[22px]">
          {members?.map((member, index) => (
            <div key={index} className="w-[252px] overflow-hidden">
              <img
                src={member.image.__image_url__}
                alt={member.image.__image_prompt__}
                className="h-[244px] w-full object-cover"
              />
              <div
                className="h-full p-[23px]"
                style={{
                  backgroundColor: member.highlighted
                    ? "var(--primary-color,#15342D)"
                    : "var(--card-color,#FEFEFF)",
                }}
              >
                <p
                  className="text-[20px] font-semibold tracking-[2.074px] text-white"
                  style={{
                    color: member.highlighted
                      ? "var(--primary-text,#edf2f1)"
                      : "var(--primary-color,#15342D)",
                  }}
                >
                  {member.title}
                </p>
                <p
                  className="mt-[20px] text-[28px] font-normal  text-white"
                  style={{
                    color: member.highlighted
                      ? "var(--primary-text,#edf2f1)"
                      : "var(--primary-color,#15342D)",
                  }}
                >
                  {member.name}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
};

export default MeetTeamSlide;
