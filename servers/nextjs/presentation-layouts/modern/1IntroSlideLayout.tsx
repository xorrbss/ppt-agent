import React from "react";
import * as z from "zod";

export const layoutId = "intro-pitchdeck-slide";
export const layoutName = "Intro Pitch Deck Slide";
export const layoutDescription =
  "A visually appealing introduction slide for a pitch deck, featuring a large title, company name, date, and contact information with a modern design.";
const introPitchDeckSchema = z.object({
  title: z.string().min(2).max(15).default("Pitch Deck").meta({
    description: "Main title of the slide",
  }),
  description: z.string().default("").meta({
    description: "Empty description as per the design",
  }),
  contactNumber: z.string().default("+123-456-7890").meta({
    description: "Contact phone number displayed in footer",
  }),
  contactAddress: z
    .string()
    .default("123 Anywhere St., Any City, ST 123")
    .meta({
      description: "Contact address displayed in footer",
    }),
  contactWebsite: z.string().default("www.reallygreatsite.com").meta({
    description: "Contact website URL displayed in footer",
  }),
  companyName: z.string().default("presenton").meta({
    description: "Company name displayed in header",
  }),
  date: z.string().default("June 13, 2038").meta({
    description: "Date of the presentation",
  }),
});

export const Schema = introPitchDeckSchema;
export type IntroPitchDeckData = z.infer<typeof introPitchDeckSchema>;

interface IntroSlideLayoutProps {
  data?: Partial<IntroPitchDeckData>;
}

const IntroPitchDeckSlide: React.FC<IntroSlideLayoutProps> = ({
  data: slideData,
}) => {
  return (
    <>
      {/* Montserrat Font */}
      <link
        href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&display=swap"
        rel="stylesheet"
      />
      <div
        className="w-full max-w-[1280px] aspect-video mx-auto relative overflow-hidden rounded-md"
        style={{
          fontFamily: "Montserrat, sans-serif",
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      >
        {/* Top Header */}
        <div className="absolute top-8 left-10 right-10 flex justify-between items-center text-[#1E4CD9] text-sm font-semibold">
          <span>{slideData?.companyName}</span>
          <span>{slideData?.date}</span>
        </div>

        {/* Main Title */}
        <div
          className="absolute left-10"
          style={{
            top: "50%",
            transform: "translateY(-50%)",
          }}
        >
          <h1
            className="text-[9.5rem] font-bold text-[#1E4CD9] leading-none inline-block relative"
            id="pitchdeck-title"
            style={{ display: "inline-block" }}
          >
            {slideData?.title}
            {/* Blue underline */}
            <span
              className="block bg-[#1E4CD9] h-[4px] absolute left-0"
              style={{
                width: "100%",
                bottom: "-0.5em",
                transition: "width 0.3s",
              }}
            />
          </h1>
        </div>

        {/* Bottom Contact Row */}
        <div className="absolute bottom-8 left-10 right-10 flex flex-wrap items-center gap-10 text-[#1E4CD9] text-sm font-medium">
          <div className="flex items-center gap-2">
            <span className="text-lg">📞</span>
            <span>{slideData?.contactNumber}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-lg">📍</span>
            <span>{slideData?.contactAddress}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-lg">🌐</span>
            <span>{slideData?.contactWebsite}</span>
          </div>
        </div>
      </div>
    </>
  );
};

export default IntroPitchDeckSlide;
