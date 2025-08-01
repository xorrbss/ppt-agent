import React, { useEffect, useState } from "react";
import * as Babel from "@babel/standalone";
import * as z from "zod";

// Clean JSX code without imports - they'll be available in the execution context
const jsxCode = `
const ImageSchema = z.object({
    __image_url__: z.url().meta({
        description: "URL to image",
    }),
    __image_prompt__: z.string().meta({
        description: "Prompt used to generate the image",
    }).min(10).max(50),
})

const layoutId = 'title-slide-with-decorative-elements'
const layoutName = 'TitleSlideWithDecorativeElements'
const layoutDescription = 'A title slide layout with company name, main title, subtitle, author text, and decorative curved shapes with images.'

const titleSlideWithDecorativeElementsSchema = z.object({
    companyName: z.string().min(5).max(30).default('AROWWAI INDUSTRIES').meta({
        description: "Company or organization name",
    }),
    mainTitle: z.string().min(5).max(50).default('STRATEGY DECK').meta({
        description: "Main title of the presentation (can include line breaks)",
    }),
    subtitle: z.string().min(10).max(80).default('STRATEGIES FOR GROWTH AND INNOVATION').meta({
        description: "Subtitle describing the presentation topic",
    }),
    authorText: z.string().min(5).max(30).default('BY GROUP 1').meta({
        description: "Author or presenter information",
    }),
    logo: ImageSchema.default({
        __image_url__: 'https://images.pexels.com/photos/31995895/pexels-photo-31995895/free-photo-of-turkish-coffee-with-scenic-bursa-view.jpeg',
        __image_prompt__: 'Company logo or brand icon'
    }).meta({
        description: "Company logo or brand icon",
    }),
    leftDecorativeImage: ImageSchema.default({
        __image_url__: 'https://images.pexels.com/photos/31995895/pexels-photo-31995895/free-photo-of-turkish-coffee-with-scenic-bursa-view.jpeg',
        __image_prompt__: 'Turkish coffee with scenic Bursa view'
    }).meta({
        description: "Left decorative curved shape background image",
    }),
    rightDecorativeImage: ImageSchema.default({
        __image_url__: 'https://images.pexels.com/photos/31995895/pexels-photo-31995895/free-photo-of-turkish-coffee-with-scenic-bursa-view.jpeg',
        __image_prompt__: 'Turkish coffee with scenic Bursa view'
    }).meta({
        description: "Right decorative curved shape background image",
    })
})

const Schema = titleSlideWithDecorativeElementsSchema

const TitleSlideWithDecorativeElementsLayout = ({ data: slideData }) => {
    // Split main title by newlines for proper rendering
    const titleLines = (slideData?.mainTitle || 'STRATEGY DECK').split('\\n')

    return (
        <>
            {/* Import Google Fonts */}
            <link 
                href="https://fonts.googleapis.com/css2?family=League+Spartan:wght@400;500;600;700&display=swap" 
                rel="stylesheet"
            />
            <link 
                href="https://fonts.googleapis.com/css2?family=Futura:wght@400;500;600&display=swap" 
                rel="stylesheet"
            />
            
            <div 
                className=" w-full rounded-sm max-w-[1280px] shadow-lg max-h-[720px] aspect-video bg-white relative z-20 mx-auto overflow-hidden"
                style={{ backgroundColor: '#8d7b68' }}
            >
                {/* Bottom horizontal line */}
                <div 
                    className="absolute bottom-0 left-0 w-full h-0.5 bg-yellow-50" 
                    style={{ backgroundColor: '#fdf7e4' }}
                ></div>
                
                {/* Upper horizontal line */}
                <div 
                    className="absolute top-20 left-48 w-80 h-0.5 bg-yellow-50" 
                    style={{ backgroundColor: '#fdf7e4' }}
                ></div>
                
                {/* Right vertical line */}
                <div 
                    className="absolute top-0 right-8 w-0.5 h-full bg-yellow-50" 
                    style={{ backgroundColor: '#fdf7e4' }}
                ></div>
                
                {/* Upper left circular element */}
                <div 
                    className="absolute top-8 left-24 w-20 h-20 rounded-full" 
                    style={{ backgroundColor: '#a4907c' }}
                ></div>
                
                {/* Lower right circular element */}
                <div 
                    className="absolute bottom-20 right-28 w-48 h-48 rounded-full" 
                    style={{ backgroundColor: '#a4907c' }}
                ></div>
                
                {/* Left decorative curved shape */}
                <div className="absolute top-20 left-0 w-64 h-80 overflow-hidden">
                    <svg viewBox="0 0 660 996" className="w-full h-full">
                        <path 
                            d="M220.252 19.07C254 7.556 292.6 0 330.378 0C368.157 0 404.509 6.476 438.009 17.99C438.723 18.35 439.435 18.35 440.148 18.71C565.955 64.765 658.618 186.379 660.4 332.57L660.4 995.919L0 995.919L0 333.062C1.782 185.66 93.019 64.045 220.252 19.07Z" 
                            fill="url(#leftImage)"
                        />
                        <defs>
                            <pattern id="leftImage" patternUnits="objectBoundingBox" width="1" height="1">
                                <image 
                                    href={slideData?.leftDecorativeImage?.__image_url__ || 'https://images.pexels.com/photos/31995895/pexels-photo-31995895/free-photo-of-turkish-coffee-with-scenic-bursa-view.jpeg'} 
                                    x="0" 
                                    y="0" 
                                    width="1" 
                                    height="1" 
                                    preserveAspectRatio="xMidYMid slice"
                                />
                            </pattern>
                        </defs>
                    </svg>
                </div>
                
                {/* Right decorative curved shape */}
                <div className="absolute top-32 right-0 w-80 h-96 overflow-hidden">
                    <svg viewBox="0 0 660 996" className="w-full h-full">
                        <path 
                            d="M220.252 19.07C254 7.556 292.6 0 330.378 0C368.157 0 404.509 6.476 438.009 17.99C438.723 18.35 439.435 18.35 440.148 18.71C565.955 64.765 658.618 186.379 660.4 332.57L660.4 995.919L0 995.919L0 333.062C1.782 185.66 93.019 64.045 220.252 19.07Z" 
                            fill="url(#rightImage)"
                        />
                        <defs>
                            <pattern id="rightImage" patternUnits="objectBoundingBox" width="1" height="1">
                                <image 
                                    href={slideData?.rightDecorativeImage?.__image_url__ || 'https://images.pexels.com/photos/31995895/pexels-photo-31995895/free-photo-of-turkish-coffee-with-scenic-bursa-view.jpeg'} 
                                    x="0" 
                                    y="0" 
                                    width="1" 
                                    height="1" 
                                    preserveAspectRatio="xMidYMid slice"
                                />
                            </pattern>
                        </defs>
                    </svg>
                </div>
                
                {/* Small icon/logo near company name */}
                <div className="absolute top-16 right-56 w-16 h-12 overflow-hidden">
                    <img 
                        src={slideData?.logo?.__image_url__ || 'https://images.pexels.com/photos/31995895/pexels-photo-31995895/free-photo-of-turkish-coffee-with-scenic-bursa-view.jpeg'} 
                        alt={slideData?.logo?.__image_prompt__ || 'Company logo'} 
                        className="w-full h-full object-cover"
                    />
                </div>
                
                {/* Company name */}
                <div className="absolute top-14 right-8 text-right">
                    <h2 
                        className="text-yellow-50 text-lg font-normal tracking-wider" 
                        style={{ fontFamily: "'Futura', sans-serif", color: '#fdf7e4' }}
                    >
                        {slideData?.companyName || 'AROWWAI INDUSTRIES'}
                    </h2>
                </div>
                
                {/* Main title */}
                <div className="absolute top-64 left-64 right-8 text-right">
                    <h1 
                        className="text-yellow-50 text-8xl font-normal tracking-wider leading-tight" 
                        style={{ fontFamily: "'League Spartan', sans-serif", color: '#fdf7e4' }}
                    >
                        {titleLines.map((line, index) => (
                            <React.Fragment key={index}>
                                {line}
                                {index < titleLines.length - 1 && <br />}
                            </React.Fragment>
                        ))}
                    </h1>
                </div>
                
                {/* Subtitle */}
                <div className="absolute bottom-24 left-64 right-8 text-right">
                    <h3 
                        className="text-yellow-50 text-xl font-normal tracking-wide" 
                        style={{ fontFamily: "'Futura', sans-serif", color: '#fdf7e4' }}
                    >
                        {slideData?.subtitle || 'STRATEGIES FOR GROWTH AND INNOVATION'}
                    </h3>
                </div>
                
                {/* Bottom left text */}
                <div className="absolute bottom-4 left-8">
                    <p 
                        className="text-yellow-50 text-2xl font-normal tracking-wide" 
                        style={{ fontFamily: "'Futura', sans-serif", color: '#fdf7e4' }}
                    >
                        {slideData?.authorText || 'BY GROUP 1'}
                    </p>
                </div>
            </div>
        </>
    )
}

// Return the component

`;

interface Layout {
  presentation_id: string;
  layout_id: string;
  layout_name: string;
  layout_code: string;
}

const CustomLayouts = () => {
  const [layouts, setLayouts] = useState<Layout[]>([]);
  const [component, setComponent] = useState<React.ComponentType<{
    data: any;
  }> | null>(null);

  useEffect(() => {
    const fetchLayouts = async () => {
      try {
        const res = await fetch(
          "/api/v1/ppt/layout-management/get-layouts/6038f1cb-80cb-448c-83cc-f6cb96081943"
        );
        const data = await res.json();
        setLayouts(data.layouts);
      } catch (error) {
        console.error("Failed to fetch layouts:", error);
      }
    };

    fetchLayouts();
  }, []);

  useEffect(() => {
    if (layouts.length === 0) return;

    try {
      /* ---------- 1. compile JSX to plain script ------------------ */
      const compiled = Babel.transform(jsxCode, {
        presets: [
          ["react", { runtime: "classic" }],
          ["typescript", { isTSX: true, allExtensions: true }],
        ],
        sourceType: "script",
      }).code;

      /* ---------- 2. wrap compiled code --------------------------- */
      const factory = new Function(
        "React",
        "z",
        `
        ${compiled}

        /* everything declared in the string is in scope here */
        return {
        __esModule: true,   
         default: TitleSlideWithDecorativeElementsLayout,
          layoutName,
          layoutId,
          layoutDescription,
          Schema
        };
      `
      );

      /* ---------- 4. split result --------------------------------- */
      const mod = factory(React, z);
      console.log("generated", mod);

      // default export (the component)
      const DynamicComponent = mod.default;

      // named exports (meta data)
      console.log(mod.layoutName);
      console.log(mod.layoutId);
      console.log(mod.layoutDescription);
      console.log(mod.Schema);
      const jsonSchema = z.toJSONSchema(mod.Schema, {
        override: (ctx) => {
          delete ctx.jsonSchema.default;
        },
      });
      console.log(jsonSchema);

      // tell React to render the component only
      setComponent(() => DynamicComponent);
    } catch (err: any) {
      console.error("Compilation error:", err);
      setComponent(() => () => (
        <div style={{ color: "red", padding: 20 }}>
          <h3>Compilation Error</h3>
          <pre>{err.message}</pre>
        </div>
      ));
    }
  }, [layouts]);
  z;

  return (
    <div>
      <h1>Custom Layout Renderer</h1>
      {component && (
        <div style={{ marginBottom: "20px" }}>
          <h2>Rendered Component:</h2>
          {React.createElement(component, { data: {} })}
        </div>
      )}
      {layouts.map((layout) => (
        <div key={layout.layout_id} style={{ marginBottom: "20px" }}>
          <h2>{layout.layout_name}</h2>
          <details>
            <summary>View Code</summary>
            <pre
              style={{
                background: "#f5f5f5",
                padding: "10px",
                overflow: "auto",
              }}
            >
              {layout.layout_code}
            </pre>
          </details>
        </div>
      ))}
    </div>
  );
};

export default CustomLayouts;
