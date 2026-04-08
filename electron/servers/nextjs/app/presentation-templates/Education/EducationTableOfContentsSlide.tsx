import * as z from "zod";


export const slideLayoutId = "education-table-of-contents-slide";
export const slideLayoutName = "Education Table Of Contents Slide";
export const slideLayoutDescription =
  "A split layout with a left title panel and a right list of numbered sections, with one subtle background image overlay.";

const TocItemSchema = z.object({
  number: z.string().min(2).max(3).meta({
    description: "Section number displayed before each section title.",
  }),
  label: z.string().min(3).max(30).meta({
    description: "Section title shown in the right column list.",
  }),
});

export const Schema = z.object({
  titleLine1: z.string().min(4).max(12).default("Table of").meta({
    description: "First line of the left-side heading.",
  }),
  titleLine2: z.string().min(4).max(12).default("Contents").meta({
    description: "Second line of the left-side heading.",
  }),
  items: z
    .array(TocItemSchema)
    .min(8)
    .max(8)
    .default([
      { number: "03", label: "ABOUT" },
      { number: "04", label: "TIMELINE" },
      { number: "05", label: "GROUP OF COMPANIES" },
      { number: "06", label: "SERVICES" },
      { number: "07", label: "IMAGE GALLERY" },
      { number: "08", label: "STATISTICS" },
      { number: "09", label: "REPORT" },
      { number: "10", label: "CONCLUSION" },
    ])
    .meta({
      description: "Eight table-of-content entries listed on the right.",
    }),

});

export type SchemaType = z.infer<typeof Schema>;

const EducationTableOfContentsSlide = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <div
      className="relative h-[720px] w-[1280px] overflow-hidden"
      style={{
        backgroundColor: "var(--background-color,#efeff1)",
        fontFamily: "var(--body-font-family,'Times New Roman')",
      }}
    >


      <div className="relative z-10 grid h-full grid-cols-[430px_1fr]">
        <div className="px-[56px] pt-[74px]" style={{ backgroundColor: "var(--card-color,#f1efef)" }}>
          <h2 className="font-serif text-[64px] leading-[98%] tracking-[-0.02em]" style={{ color: "var(--primary-color,#1a1752)" }}>
            {data.titleLine1}
            <br />
            {data.titleLine2}
          </h2>
        </div>

        <div className="px-[88px] pt-[84px]" style={{ backgroundColor: "var(--card-color,#FFFFFF80)" }}>
          <div className="space-y-[32px]">
            {data.items?.map((item, index) => (
              <div key={`${item.number}-${item.label}-${index}`} className="flex items-center gap-[16px]">
                <span className="w-[42px] text-[20px] font-semibold leading-none" style={{ color: "var(--background-text,#3f414a)" }}>
                  {item.number}
                </span>
                <span className="text-[24px] font-medium leading-none" style={{ color: "var(--background-text,#3f414a)" }}>
                  {item.label}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default EducationTableOfContentsSlide;
