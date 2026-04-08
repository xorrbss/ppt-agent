import * as z from "zod";

export const slideLayoutId = "code-table-of-content-slide";
export const slideLayoutName = "Code Table Of Content Slide";
export const slideLayoutDescription =
  "A two-column table of contents with numbered entries and folder-style bullets.";

export const Schema = z.object({
  title: z.string().min(8).max(24).default("Table of Content").meta({
    description: "Slide heading shown above the index list.",
  }),
  items: z
    .array(z.object({
      number: z.string().min(2).max(2),
      label: z.string().min(3).max(16),
      description: z.string().min(3).max(18),
    }))
    .min(12)
    .max(12)
    .default([
      { number: "01", label: "Content 1", description: "Section summary" },
      { number: "02", label: "Content 2", description: "Section summary" },
      { number: "03", label: "Content 3", description: "Section summary" },
      { number: "04", label: "Content 4", description: "Section summary" },
      { number: "05", label: "Content 5", description: "Section summary" },
      { number: "06", label: "Content 6", description: "Section summary" },
      { number: "07", label: "Content 7", description: "Section summary" },
      { number: "08", label: "Content 8", description: "Section summary" },
      { number: "09", label: "Content 9", description: "Section summary" },
      { number: "10", label: "Content 10", description: "Section summary" },
      { number: "11", label: "Content 11", description: "Section summary" },
      { number: "12", label: "Content 12", description: "Section summary" },
    ])
    .meta({
      description: "Left-column table of contents entries.",
    }),

  pageLabel: z.string().min(3).max(8).default("9 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

function TocColumn({ items }: { items: { number: string; label: string, description?: string }[] }) {
  return (
    <div className="space-y-[26px]">
      {items.map((item, index) => {


        return (
          <div key={`${item.number}-${item.label}`} className="flex items-center gap-[10px]" style={{ color: "var(--background-text,#d5dcff)" }}>
            <div className=""><svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22" fill="none">
              <path d="M17.7778 17.7776C18.2493 17.7776 18.7015 17.5903 19.0349 17.2569C19.3683 16.9235 19.5556 16.4713 19.5556 15.9998V7.11095C19.5556 6.63945 19.3683 6.18727 19.0349 5.85387C18.7015 5.52047 18.2493 5.33317 17.7778 5.33317H10.7556C10.4583 5.33609 10.165 5.26438 9.90254 5.12462C9.6401 4.98486 9.41691 4.7815 9.25339 4.53317L8.53339 3.4665C8.37151 3.2207 8.15114 3.01893 7.89205 2.8793C7.63296 2.73967 7.34326 2.66655 7.04894 2.6665H3.55561C3.08411 2.6665 2.63193 2.8538 2.29853 3.1872C1.96513 3.5206 1.77783 3.97279 1.77783 4.44428V15.9998C1.77783 16.4713 1.96513 16.9235 2.29853 17.2569C2.63193 17.5903 3.08411 17.7776 3.55561 17.7776H17.7778Z" stroke="var(--primary-color,#51A2FF)" strokeWidth="1.77778" strokeLinecap="round" strokeLinejoin="round" />
            </svg></div>
            <div className="flex gap-[26px]">

              <p className="w-[36px] text-[18px]" style={{ color: "var(--primary-color,#8EC5FF)" }}>{item.number}</p>
              <p className="text-[18px]" style={{ color: "var(--background-text,#ffffff)" }}>{item.label}</p>
              {item.description && <p className="text-[18px]" style={{ color: "var(--background-text,#ffffff)" }}>{item.description}</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const CodeSlide09TableOfContent = ({ data }: { data: Partial<SchemaType> }) => {
  const leftItems = data?.items?.slice(0, data?.items?.length / 2);
  const rightItems = data?.items?.slice(data?.items?.length / 2);


  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden p-[53px]"
      style={{
        backgroundColor: "var(--background-color,#101B37)",
        fontFamily: "var(--body-font-family,Nunito Sans)",
      }}
    >


      <div className="relative z-10 flex h-full flex-col">
        <h2 className="text-[64px] font-medium" style={{ color: "var(--background-text,#f2f4ff)" }}>{data.title}</h2>

        <div className="mt-[53px] grid flex-1 grid-cols-2 gap-[24px]">
          <TocColumn items={leftItems || []} />
          <TocColumn items={rightItems || []} />
        </div>
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

export default CodeSlide09TableOfContent;
