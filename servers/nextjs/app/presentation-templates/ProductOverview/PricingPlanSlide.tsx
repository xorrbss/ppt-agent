import { RemoteSvgIcon } from "@/app/hooks/useRemoteSvgIcon";
import * as z from "zod";


export const slideLayoutId = "title-cards-list-with-text-slide";
export const slideLayoutName = "제목과 텍스트 카드 목록 슬라이드";
export const slideLayoutDescription =
  "상단에 제목이 있고, 텍스트 내용이 담긴 카드 목록으로 구성된 콘텐츠 섹션이 있는 슬라이드.";

const PlanSchema = z.object({
  price: z.string().min(4).max(12).meta({
    description: "Plan price label shown at the top of each card.",
  }),
  description: z.string().max(18).meta({
    description: "Short statement describing the plan.",
  }),
  features: z
    .array(z.string().max(16))

    .max(4)
    .meta({
      description: "Four bullet features shown in the pricing card.",
    }),
  highlighted: z.boolean().default(false).meta({
    description: "Whether this card uses the highlighted dark style.",
  }),
});

export const Schema = z.object({
  title: z.string().min(6).max(18).default("가격 플랜").meta({
    description: "Main slide title.",
  }),
  featureIcon: z.object({
    __icon_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg"),
    __icon_query__: z.string().min(3).max(30).default("check icon"),
  }).default({
    __icon_url__:
      "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
    __icon_query__: "check icon",
  }).meta({
    description: "Icon used for each feature bullet in plan cards.",
  }),
  plans: z
    .array(PlanSchema)
    .max(3)
    .default([
      {
        price: "$80/월",
        description: "예시 설명입니다.",
        features: [
          "예시 기능입니다.",
          "예시 기능입니다.",
          "예시 기능입니다.",
          "예시 기능입니다.",
        ],
        highlighted: false,
      },
      {
        price: "$80/월",
        description: "예시 설명입니다.",
        features: [
          "예시 기능입니다.",
          "예시 기능입니다.",
          "예시 기능입니다.",
          "예시 기능입니다.",
        ],
        highlighted: true,
      },
      {
        price: "$80/월",
        description: "예시 설명입니다.",
        features: [
          "예시 기능입니다.",
          "예시 기능입니다.",
          "예시 기능입니다.",
          "예시 기능입니다.",
        ],
        highlighted: false,
      },
    ])
    .meta({
      description: "Three pricing cards displayed across the slide.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const PricingPlanSlide = ({ data }: { data: Partial<SchemaType> }) => {
  const { title, featureIcon, plans } = data;

  return (
    <>
      <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,200..800&display=swap" rel="stylesheet" />
      <div
        className="relative h-[720px] w-[1280px] overflow-hidden flex flex-col "
        style={{
          backgroundColor: "var(--background-color,#DAE1DE)",
          fontFamily: "var(--body-font-family,'Bricolage Grotesque')",
        }}
      >
        <div className="px-[68px] pt-[50px]">
          <h2
            className="text-[80px] font-semibold leading-[108.4%] tracking-[-2.419px] text-[#15342D]"
            style={{ color: "var(--primary-color,#15342D)" }}
          >
            {title}
          </h2>
        </div>

        <div className=" px-[68px] mt-16  grid grid-cols-3 items-start">
          {plans?.map((plan, index) => {
            const active = plan.highlighted;
            return (
              <div
                key={index}
                className={` px-[20px]  ${active ? "-mt-[30px] py-[60px]" : "py-[33px]"}`}
                style={{
                  backgroundColor: active
                    ? "var(--primary-color,#15342D)"
                    : "var(--card-color,#ececee)",
                }}
              >
                <p
                  className="text-[20px] font-semibold tracking-[2.074px] text-white"
                  style={{
                    color: active
                      ? "var(--primary-text,#edf2f1)"
                      : "var(--primary-color,#15342D)",
                  }}
                >
                  {plan.price}
                </p>
                <p
                  className="mt-[18px] text-[28px] font-normal  text-[#15342DCC]"
                  style={{
                    color: active
                      ? "var(--primary-text,#edf2f1)"
                      : "var(--background-text,#15342DCC)",
                  }}
                >
                  {plan.description}
                </p>

                <div className="mt-[18px] space-y-[6px]">
                  {plan.features.map((feature, featureIndex) => (
                    <div key={featureIndex} className="flex items-center gap-[10px]">
                      <RemoteSvgIcon
                        url={featureIcon?.__icon_url__}
                        strokeColor={"currentColor"}
                        className="w-[28px] h-[28px] object-contain"
                        color={active ? "var(--primary-text, #edf2f1)" : "var(--background-text, #15342DCC)"}
                        title={featureIcon?.__icon_query__}
                      />
                      {/* <img
                        src={featureIcon?.__icon_url__}
                        alt={featureIcon?.__icon_query__}
                        className="h-[28px] w-[28px] object-contain"
                        style={{ filter: active ? "brightness(0) invert(1)" : "none" }}
                      /> */}
                      <p
                        className="text-[28px] font-normal  text-[#15342DCC]"
                        style={{
                          color: active
                            ? "var(--primary-text,#edf2f1)"
                            : "var(--background-text,#15342DCC)",
                        }}
                      >
                        {feature}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
};

export default PricingPlanSlide;
