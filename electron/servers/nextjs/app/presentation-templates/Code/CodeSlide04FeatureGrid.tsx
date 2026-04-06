import * as z from "zod";

const FeatureCardSchema = z.object({
  title: z.string().min(3).max(20).meta({
    description: "Feature title shown on each card.",
  }),
  description: z.string().min(18).max(82).meta({
    description: "Supporting feature description.",
  }),
  icon: z.object({
    __icon_url__: z.string().min(10).max(180).meta({
      description: "URL to icon",
    }),
    __icon_query__: z.string().min(3).max(28).meta({
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
        title: "Component Library",
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
    <div className="relative h-[720px] w-[1280px] overflow-hidden  bg-[#101B37] p-[53px] ">


      <h2 className="text-[64px] font-medium tracking-[-0.03em] text-[#f2f4ff]">{data.title}</h2>

      <div className="mt-[26px] grid flex-1 grid-cols-3 items-center h-fit  gap-[26px]">
        {data?.features?.map((feature) => (
          <div
            key={feature.title}
            style={{
              boxShadow: "0 33.333px 66.667px -16px rgba(0, 0, 0, 0.25)",

            }}
            className="rounded-[18px] border border-[#1D293D80] bg-[#0F172B80] p-[26px]"
          >
            <div className="flex items-start justify-between gap-[8px]">
              <h3 className="text-[26px] font-medium text-[#ffffff]">{feature.title}</h3>
              <span className="flex h-[52px] w-[52px] items-center justify-center rounded-full border border-[#2B7FFF4D] bg-[#2B7FFF33] text-[18px] ">
                <img src={feature.icon.__icon_url__} alt={feature.icon.__icon_query__} className="h-[24px] w-[24px] object-contain"
                  style={{
                    filter: "invert(1)",
                  }}
                />
              </span>
            </div>
            <p className="mt-[12px] text-[18px] leading-[136%] text-[#90A1B9]">{feature.description}</p>
          </div>
        ))}
      </div>


      <div className="absolute bottom-[26px] z-50 left-1/2 -translate-x-1/2 rounded-full border border-[#31415880] bg-[#1D293DCC] px-[22px] py-[8px] text-[14px] text-[#CAD5E2]">
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide04FeatureGrid;
