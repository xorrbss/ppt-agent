import { Fragment } from "react/jsx-runtime";
import * as z from "zod";

const MetricSchema = z.object({
  value: z.string().min(1).max(6).meta({
    description: "Primary metric value shown in the card.",
  }),
  label: z.string().min(3).max(10).meta({
    description: "Short metric label shown below the value.",
  }),
  description: z.string().min(6).max(20).meta({
    description: "Supporting text shown below the label.",
  }),
});

const StatColumnSchema = z.object({
  metrics: z.array(MetricSchema).min(2).max(2).meta({
    description: "Two stacked metrics shown in one tall card.",
  }),
});

export const slideLayoutId = "introduction-stats-slide";
export const slideLayoutName = "Introduction Stats Slide";
export const slideLayoutDescription =
  "A slide with a title and explanatory text on the left, a bulleted list underneath the text, and two tall metric cards placed side by side on the right. Each metric card contains two stacked metric blocks.";

export const Schema = z.object({
  title: z.string().min(3).max(12).default("Introduction").meta({
    description: "Slide title shown at the top-left.",
  }),
  body: z.string().max(250).default(
    "Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut alut enim ad minima veniam, quis. Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut alut enim ad minima veniam, quis"
  ).meta({
    description: "Primary paragraph shown below the title.",
  }),
  bullets: z
    .array(z.string().max(35))

    .max(4)
    .default([
      "Ut enim ad minima veniam, quis nostrum",
      "Exercitationem ullam corporis suscipit",
      "Ut enim ad minima veniam, quis nostrum",
      "exercitationem ullam corporis suscipit",
    ])
    .meta({
      description: "Bullet list shown in the lower-left area.",
    }),
  statColumns: z
    .array(StatColumnSchema)

    .max(2)
    .default([
      {
        metrics: [
          { value: "25K", label: "Students", description: "Ut enim ad minima" },
          { value: "25K", label: "Students", description: "Ut enim ad minima" },
        ],
      },
      {
        metrics: [
          { value: "25K", label: "Students", description: "Ut enim ad minima" },
          { value: "25K", label: "Students", description: "Ut enim ad minima" },
        ],
      },
    ])
    .meta({
      description: "Two stat cards shown on the right side of the slide.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

type StatMetric = {
  value: string;
  label: string;
  description: string;
};

function StatPill({
  metrics,

}: {
  metrics: StatMetric[];

}) {


  return (
    <div
      className=" h-[438px] w-[248px] overflow-hidden rounded-[127px] bg-[#157CFF] px-[28px] py-[74px] text-center text-white"
      style={{
        backgroundColor: "var(--primary-color,#157CFF)",
        color: "var(--primary-text,#ffffff)",
      }}
    >

      {metrics.map((metric, index) => (
        <Fragment key={`${metric.value}-${metric.label}-${index}`}>
          <div
            key={`${metric.value}-${metric.label}-${index}`}
            className={``}
          >
            <p className="text-[55px] font-medium leading-[ 44.353px] tracking-[-1.09px]">
              {metric.value}
            </p>
            <p className="mt-[6px] text-[20px] font-medium leading-none">{metric.label}</p>
            <p className=" text-[20px] leading-[1.15] text-white/90" style={{ color: "var(--primary-text,#ffffff)", opacity: 0.9 }}>
              {metric.description}
            </p>
          </div>
          {index === 0 && <div className="py-[22px]">

            <svg xmlns="http://www.w3.org/2000/svg" width="181" height="1" viewBox="0 0 181 1" fill="none">
              <path
                opacity="0.2"
                d="M0 0.487305H180.122"
                stroke="var(--primary-text,#ffffff)"
                strokeWidth="0.974913"
                strokeDasharray="3.9 1.95"
              />
            </svg>
          </div>
          }
        </Fragment>
      ))}


    </div>
  );
}

const IntroductionStatsSlide = ({ data }: { data: Partial<SchemaType> }) => {

  const { title, body, bullets, statColumns } = data;

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-[#f9f8f8]"
      style={{
        backgroundColor: "var(--background-color,#f9f8f8)",
        fontFamily: "var(--body-font-family,Nunito Sans)",
      }}
    >
      <div
        className="absolute left-0 top-0 w-[42px] rounded-b-[22px] bg-[#157CFF]"
        style={{ height: 185, backgroundColor: "var(--primary-color,#157CFF)" }}
      />

      <div className="px-[64px] pt-[48px]">
        <h2
          className="text-[80px] font-bold leading-[108.4%] tracking-[-2.419px] text-[#232223]"
          style={{ color: "var(--background-text,#232223)" }}
        >
          {title}
        </h2>
      </div>

      <div className="flex justify-between px-[96px] pt-[38px]">
        <div className="">
          <p className="max-w-[400px] text-[24px] leading-[26.667px] text-[#232223]" style={{ color: "var(--background-text,#232223)" }}>
            {body}
          </p>

          <ul
            className="mt-[34px] list-disc pl-[28px] text-[24px] leading-[26.667px] text-[#232223]"
            style={{ color: "var(--background-text,#232223)" }}
          >
            {bullets?.map((bullet, index) => (
              <li key={`${bullet}-${index}`} className="mt-[8px]">
                {bullet}
              </li>
            ))}
          </ul>
        </div>

        <div className="ml-[48px] flex gap-[34px]">
          {statColumns?.map((column, index) => (
            <StatPill key={`intro-stat-column-${index}`} metrics={column.metrics} />
          ))}
        </div>
      </div>
    </div>
  );
};

export default IntroductionStatsSlide;
