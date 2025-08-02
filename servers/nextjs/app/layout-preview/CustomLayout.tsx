import React, { useState } from "react";
import * as Babel from "@babel/standalone";
import * as z from "zod";
const jsxCode = `
\nconst ImageSchema = z.object({\n    __image_url__: z.string().meta({\n        description: \"URL to image\",\n    }),\n    __image_prompt__: z.string().meta({\n        description: \"Prompt used to generate the image\",\n    }).min(10).max(50),\n})\n\nconst IconSchema = z.object({\n    __icon_url__: z.string().meta({\n        description: \"URL to icon\",\n    }),\n    __icon_query__: z.string().meta({\n        description: \"Query used to search the icon\",\n    }).min(5).max(20),\n})\n\nconst layoutId = \"company-about-background-overlay-slide\"\nconst layoutName = \"CompanyAboutBackgroundOverlayLayout\" \nconst layoutDescription = \"A slide with background image, company logo, main title with overlay, and description text\"\n\nconst companyAboutBackgroundOverlaySlideSchema = z.object({\n    backgroundImage: ImageSchema.default({\n        __image_url__: \"/static/images/placeholder.jpg\",\n        __image_prompt__: \"Corporate office building or business background\"\n    }).meta({\n        description: \"Background image for the slide\",\n    }),\n    companyLogo: IconSchema.default({\n        __icon_url__: \"/static/icons/placeholder.png\",\n        __icon_query__: \"company logo circle\"\n    }).meta({\n        description: \"Company logo icon\",\n    }),\n    companyName: z.string().min(2).max(25).default(\"Arowwai Industries\").meta({\n        description: \"Company name text\",\n    }),\n    mainTitle: z.string().min(3).max(35).default(\"ABOUT COMPANY\").meta({\n        description: \"Main title text\",\n    }),\n    description: z.string().min(50).max(600).default(\"Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.\").meta({\n        description: \"Description text below the main title\",\n    })\n})\n\nconst Schema = companyAboutBackgroundOverlaySlideSchema\n\ntype CompanyAboutBackgroundOverlaySlideData = z.infer<typeof companyAboutBackgroundOverlaySlideSchema>\n\ninterface CompanyAboutBackgroundOverlaySlideLayoutProps {\n    data?: Partial<CompanyAboutBackgroundOverlaySlideData>\n}\n\nconst dynamicSlideLayout: React.FC<CompanyAboutBackgroundOverlaySlideLayoutProps> = ({ data: slideData }) => {\n    return (\n        <div className=\"relative w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white relative z-20 mx-auto overflow-hidden\">\n            {/* Background image */}\n            <img \n                src={slideData?.backgroundImage?.__image_url__ || \"/static/images/placeholder.jpg\"} \n                alt={slideData?.backgroundImage?.__image_prompt__ || \"Background\"}\n                className=\"absolute top-0 left-0 w-full h-[470px] object-cover\"\n            />\n            \n            {/* Dark overlay rectangle */}\n            <div className=\"absolute top-[198px] left-[230px] w-[819px] h-[183px] bg-black bg-opacity-50\"></div>\n            \n            {/* Yellow triangle in top right */}\n            <svg className=\"absolute top-0 right-0 w-[85px] h-[85px]\" viewBox=\"0 0 85 85\">\n                <polygon points=\"0,85 0,0 85,85\" fill=\"#dfeb33\"/>\n            </svg>\n            \n            {/* Logo icon */}\n            <div className=\"absolute top-[63px] left-[99px] w-[17px] h-[17px] bg-green-500 rounded-full\"></div>\n            \n            {/* Company name */}\n            <div \n                className=\"absolute top-[61px] left-[129px] text-white font-medium\" \n                style={{\n                    fontFamily: \"'Open Sans', sans-serif\", \n                    fontSize: \"15px\"\n                }}\n            >\n                {slideData?.companyName || \"Arowwai Industries\"}\n            </div>\n            \n            {/* Main title */}\n            <div className=\"absolute top-[232px] left-[230px] w-[819px] h-[106px] flex items-center justify-center\">\n                <span \n                    className=\"text-[#dfeb33] font-bold text-center\" \n                    style={{\n                        fontFamily: \"'Raleway', sans-serif\", \n                        fontSize: \"64px\", \n                        letterSpacing: \"0.02em\"\n                    }}\n                >\n                    {slideData?.mainTitle || \"ABOUT COMPANY\"}\n                </span>\n            </div>\n            \n            {/* Description text */}\n            <div \n                className=\"absolute top-[544px] left-[184px] w-[911px] text-center leading-relaxed\" \n                style={{\n                    fontFamily: \"'Open Sans', sans-serif\", \n                    fontSize: \"16px\", \n                    color: \"#202b3d\"\n                }}\n            >\n                {slideData?.description || \"Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur.\"}\n            </div>\n            \n            {/* Yellow line */}\n            <div className=\"absolute top-[644px] left-[581px] w-[117px] h-[3px] bg-[#dfeb33] rounded-full\"></div>\n        </div>\n    )\n}\n
`;

const CustomLayout = () => {
  const data = compileCustomLayout(jsxCode, React, z);

  const {
    default: DynamicSlideLayout,
    layoutName,
    layoutId,
    layoutDescription,
    Schema,
  } = data;

  console.log(data);

  return (
    <div>
      CustomLayout
      <DynamicSlideLayout />
    </div>
  );
};

export default CustomLayout;

const compileCustomLayout = (layoutCode: string, React: any, z: any) => {
  const cleanCode = layoutCode
    .replace(/import\s+React\s+from\s+'react';?/g, "")
    .replace(/import\s*{\s*z\s*}\s*from\s+'zod';?/g, "")
    .replace(/import\s+.*\s+from\s+['"]zod['"];?/g, "")
    // remove every zod import (any style)
    .replace(/import\s+.*\s+from\s+['"]zod['"];?/g, "")
    .replace(/const\s+[^=]*=\s*require\(['"]zod['"]\);?/g, "");
  const compiled = Babel.transform(cleanCode, {
    presets: [
      ["react", { runtime: "classic" }],
      ["typescript", { isTSX: true, allExtensions: true }],
    ],
    sourceType: "script",
  }).code;

  const factory = new Function(
    "React",
    "_z",
    `
    const z = _z;
      ${compiled}

      /* everything declared in the string is in scope here */
      return {
        __esModule: true,   
        default: dynamicSlideLayout,
        layoutName,
        layoutId,
        layoutDescription,
        Schema
      };
    `
  );

  return factory(React, z);
};
