import * as z from "zod";

const FeatureCardSchema = z.object({
  title: z.string().min(3).max(17).meta({
    description: "Feature title shown on each card.",
  }),
  description: z.string().min(18).max(80).meta({
    description: "Supporting feature description.",
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
    description: "Icon used for each feature bullet in plan cards.",
  }),
});

export const slideLayoutId = "code-feature-grid-slide";
export const slideLayoutName = "Code Feature Grid Slide";
export const slideLayoutDescription =
  "A six-card feature summary grid with icon badges and compact descriptions.";

export const Schema = z.object({
  title: z.string().min(6).max(20).default("Feature Grid").meta({
    description: "Slide title shown above the grid.",
  }),
  features: z
    .array(FeatureCardSchema)
    .min(3)
    .max(6)
    .default([
      {
        title: "Modern Stack",
        description: "Built with React, TypeScript, and Tailwind CSS for maximum developer experience.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "Component Library ",
        description: "Reusable UI components with consistent design patterns.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "CLI Tools",
        description: "Command-line utilities for scaffolding and automation.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "Analytics",
        description: "Built-in tracking and performance monitoring.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "Version Control",
        description: "Git-based workflow with automated deployments.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
      {
        title: "Best Practices",
        description: "Following industry standards and modern development patterns.",
        icon: {
          __icon_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/icons/placeholder.svg",
          __icon_query__: "check icon",
        },
      },
    ])
    .meta({
      description: "Six feature cards displayed in a 3x2 grid.",
    }),
  pageLabel: z.string().min(3).max(8).default("4 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide04FeatureGrid = ({ data }: { data: Partial<SchemaType> }) => {


  return (
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
                <img src={feature.icon.__icon_url__} alt={feature.icon.__icon_query__} className="h-[24px] w-[24px] object-contain"
                  style={{
                    filter: "invert(1)",
                  }}
                />
              </span>
            </div>
            <p className="mt-[12px] text-[18px] leading-[136%]" style={{ color: "var(--background-text,#90A1B9)" }}>{feature.description}</p>
          </div>
        ))}
      </div>


      <div
        className="absolute bottom-[26px] z-50 left-1/2 -translate-x-1/2 rounded-full border px-[22px] py-[8px] text-[14px]"
        style={{
          borderColor: "var(--stroke,#31415880)",
          backgroundColor: "var(--card-color,#1D293DCC)",
          color: "var(--background-text,#CAD5E2)",
        }}
      >
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide04FeatureGrid;
