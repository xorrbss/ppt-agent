"use client";
import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  ReactNode,
} from "react";
import dynamic from "next/dynamic";
import { toast } from "sonner";
import * as z from "zod";
import { useDispatch } from "react-redux";
import { setLayoutLoading } from "@/store/slices/presentationGeneration";
import * as Babel from "@babel/standalone";
export interface LayoutInfo {
  id: string;
  name?: string;
  description?: string;
  json_schema: any;
  groupName: string;
}

export interface GroupSetting {
  description: string;
  ordered: boolean;
  default?: boolean;
}

export interface GroupedLayoutsResponse {
  groupName: string;
  files: string[];
  settings: GroupSetting | null;
}

export interface LayoutData {
  layoutsById: Map<string, LayoutInfo>;
  layoutsByGroup: Map<string, Set<string>>;
  groupSettings: Map<string, GroupSetting>;
  fileMap: Map<string, { fileName: string; groupName: string }>;
  groupedLayouts: Map<string, LayoutInfo[]>;
  layoutSchema: LayoutInfo[];
}

export interface LayoutContextType {
  getLayoutById: (layoutId: string) => LayoutInfo | null;
  getLayoutByIdAndGroup: (
    layoutId: string,
    groupName: string
  ) => LayoutInfo | null;
  getLayoutsByGroup: (groupName: string) => LayoutInfo[];
  getGroupSetting: (groupName: string) => GroupSetting | null;
  getAllGroups: () => string[];
  getAllLayouts: () => LayoutInfo[];

  loading: boolean;
  error: string | null;
  getLayout: (layoutId: string) => React.ComponentType<{ data: any }> | null;
  isPreloading: boolean;
  cacheSize: number;
  refetch: () => Promise<void>;
}

const LayoutContext = createContext<LayoutContextType | undefined>(undefined);

const layoutCache = new Map<string, React.ComponentType<{ data: any }>>();

const createCacheKey = (groupName: string, fileName: string): string =>
  `${groupName}/${fileName}`;

// Extract Babel compilation logic into a utility function
const compileCustomLayout = (layoutCode: string, React: any, z: any) => {
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
  const compiled = Babel.transform(jsxCode, {
    presets: [
      ["react", { runtime: "classic" }],
      ["typescript", { isTSX: true, allExtensions: true }],
    ],
    sourceType: "script",
  }).code;

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

  return factory(React, z);
};

export const LayoutProvider: React.FC<{
  children: ReactNode;
  presentationId?: string;
}> = ({
  children,
  presentationId = "6038f1cb-80cb-448c-83cc-f6cb96081943", // default value
}) => {
  const [layoutData, setLayoutData] = useState<LayoutData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPreloading, setIsPreloading] = useState(false);
  const dispatch = useDispatch();

  console.log("🔍 layoutData", layoutData);

  const buildData = async (groupedLayoutsData: GroupedLayoutsResponse[]) => {
    const layouts: LayoutInfo[] = [];

    const layoutsById = new Map<string, LayoutInfo>();
    const layoutsByGroup = new Map<string, Set<string>>();
    const groupSettingsMap = new Map<string, GroupSetting>();
    const fileMap = new Map<string, { fileName: string; groupName: string }>();
    const groupedLayouts = new Map<string, LayoutInfo[]>();

    // Start preloading process
    setIsPreloading(true);

    try {
      for (const groupData of groupedLayoutsData) {
        // Initialize group
        if (!layoutsByGroup.has(groupData.groupName)) {
          layoutsByGroup.set(groupData.groupName, new Set());
        }

        // group settings or default settings
        const settings = groupData.settings || {
          description: `${groupData.groupName} presentation layouts`,
          ordered: false,
          default: false,
        };

        groupSettingsMap.set(groupData.groupName, settings);
        const groupLayouts: LayoutInfo[] = [];

        for (const fileName of groupData.files) {
          try {
            const file = fileName.replace(".tsx", "").replace(".ts", "");

            const module = await import(
              `@/presentation-layouts/${groupData.groupName}/${file}`
            );
            console.log("🔍 module", module);

            if (!module.default) {
              toast.error(`${file} has no default export`, {
                description:
                  "Please ensure the layout file exports a default component",
              });
              console.warn(`❌ ${file} has no default export`);
              continue;
            }

            if (!module.Schema) {
              toast.error(`${file} has no Schema export`, {
                description: "Please ensure the layout file exports a Schema",
              });
              console.warn(`❌ ${file} has no Schema export`);
              continue;
            }

            // Cache the layout component immediately after import
            const cacheKey = createCacheKey(groupData.groupName, fileName);
            if (!layoutCache.has(cacheKey)) {
              layoutCache.set(cacheKey, module.default);
            }

            const originalLayoutId =
              module.layoutId || file.toLowerCase().replace(/layout$/, "");
            const uniqueKey = `${groupData.groupName}:${originalLayoutId}`;
            const layoutName =
              module.layoutName || file.replace(/([A-Z])/g, " $1").trim();
            const layoutDescription =
              module.layoutDescription ||
              `${layoutName} layout for presentations`;

            const jsonSchema = z.toJSONSchema(module.Schema, {
              override: (ctx) => {
                delete ctx.jsonSchema.default;
              },
            });

            const layout: LayoutInfo = {
              id: uniqueKey,
              name: layoutName,
              description: layoutDescription,
              json_schema: jsonSchema,
              groupName: groupData.groupName,
            };

            layoutsById.set(uniqueKey, layout);
            layoutsByGroup.get(groupData.groupName)!.add(uniqueKey);
            fileMap.set(uniqueKey, {
              fileName,
              groupName: groupData.groupName,
            });
            groupLayouts.push(layout);
            layouts.push(layout);
          } catch (error) {
            console.error(
              `💥 Error extracting schema for ${fileName} from ${groupData.groupName}:`,
              error
            );
          }
        }

        // Cache grouped layouts
        groupedLayouts.set(groupData.groupName, groupLayouts);
      }
    } finally {
      setIsPreloading(false);
    }

    return {
      layoutsById,
      layoutsByGroup,
      groupSettings: groupSettingsMap,
      fileMap,
      groupedLayouts,
      layoutSchema: layouts,
    };
  };

  const loadLayouts = async () => {
    try {
      setLoading(true);
      setError(null);
      dispatch(setLayoutLoading(true));

      const layoutResponse = await fetch("/api/layouts");

      if (!layoutResponse.ok) {
        throw new Error(
          `Failed to fetch layouts: ${layoutResponse.statusText}`
        );
      }

      const groupedLayoutsData: GroupedLayoutsResponse[] =
        await layoutResponse.json();

      if (!groupedLayoutsData || groupedLayoutsData.length === 0) {
        console.warn("⚠️ API returned empty data");
        setError("No layout groups found");
        return;
      }

      const data = await buildData(groupedLayoutsData);
      const customLayouts = await LoadCustomLayouts(presentationId);
      const combinedData = {
        layoutsById: mergeMaps(data.layoutsById, customLayouts.layoutsById),
        layoutsByGroup: mergeMaps(
          data.layoutsByGroup,
          customLayouts.layoutsByGroup
        ),
        groupSettings: mergeMaps(
          data.groupSettings,
          customLayouts.groupSettings
        ),
        fileMap: mergeMaps(data.fileMap, customLayouts.fileMap),
        groupedLayouts: mergeMaps(
          data.groupedLayouts,
          customLayouts.groupedLayouts
        ),
        layoutSchema: [...data.layoutSchema, ...customLayouts.layoutSchema],
      };

      setLayoutData(combinedData);

      // The preloading is now handled within buildData
    } catch (err: unknown) {
      const errorMessage =
        err instanceof Error ? err.message : "Failed to load layouts";
      setError(errorMessage);
      console.error("💥 Error loading layouts:", err);
    } finally {
      dispatch(setLayoutLoading(false));
      setLoading(false);
    }
  };

  function mergeMaps<K, V>(map1: Map<K, V>, map2: Map<K, V>): Map<K, V> {
    const merged = new Map(map1);
    map2.forEach((value, key) => {
      merged.set(key, value);
    });
    return merged;
  }

  const LoadCustomLayouts = async (presentationId: string) => {
    const layouts: LayoutInfo[] = [];

    const layoutsById = new Map<string, LayoutInfo>();
    const layoutsByGroup = new Map<string, Set<string>>();
    const groupSettingsMap = new Map<string, GroupSetting>();
    const fileMap = new Map<string, { fileName: string; groupName: string }>();
    const groupedLayouts = new Map<string, LayoutInfo[]>();

    const customLayoutResponse = await fetch(
      `/api/v1/ppt/layout-management/get-layouts/${presentationId}`
    );
    const customLayoutsData = await customLayoutResponse.json();
    const allLayout = customLayoutsData.layouts;

    const settings = {
      description: `Custom presentation layouts`,
      ordered: false,
      default: false,
    };

    groupSettingsMap.set(`custom-${presentationId}`, settings);
    const groupLayouts: LayoutInfo[] = [];
    const groupName = `custom-${presentationId}`;
    if (!layoutsByGroup.has(groupName)) {
      layoutsByGroup.set(groupName, new Set());
    }
    for (const i of allLayout) {
      try {
        /* ---------- 1. compile JSX to plain script ------------------ */
        const module = compileCustomLayout(i.layout_code, React, z);

        if (!module.default) {
          toast.error(`Custom Layout has no default export`, {
            description:
              "Please ensure the layout file exports a default component",
          });
          console.warn(`❌ Custom Layout has no default export`);
          continue;
        }

        if (!module.Schema) {
          toast.error(`Custom Layout has no Schema export`, {
            description: "Please ensure the layout file exports a Schema",
          });
          console.warn(`❌ Custom Layout has no Schema export`);
          continue;
        }
        const cacheKey = createCacheKey(
          `custom-${presentationId}`,
          i.layout_name
        );
        if (!layoutCache.has(cacheKey)) {
          layoutCache.set(cacheKey, module.default);
        }

        const originalLayoutId =
          module.layoutId || i.layout_name.toLowerCase().replace(/layout$/, "");
        const uniqueKey = `${`custom-${presentationId}`}:${originalLayoutId}`;
        const layoutName =
          module.layoutName || i.layout_name.replace(/([A-Z])/g, " $1").trim();
        const layoutDescription =
          module.layoutDescription || `${layoutName} layout for presentations`;

        const jsonSchema = z.toJSONSchema(module.Schema, {
          override: (ctx) => {
            delete ctx.jsonSchema.default;
          },
        });

        const layout: LayoutInfo = {
          id: uniqueKey,
          name: layoutName,
          description: layoutDescription,
          json_schema: jsonSchema,
          groupName: groupName,
        };

        layoutsById.set(uniqueKey, layout);
        layoutsByGroup.get(groupName)!.add(uniqueKey);
        fileMap.set(uniqueKey, {
          fileName: i.layout_name,
          groupName: groupName,
        });
        groupLayouts.push(layout);
        layouts.push(layout);
      } catch (err: any) {
        console.error("Compilation error:", err);
      }
    }
    // Cache grouped layouts
    groupedLayouts.set(groupName, groupLayouts);

    return {
      layoutsById,
      layoutsByGroup,
      groupSettings: groupSettingsMap,
      fileMap,
      groupedLayouts,
      layoutSchema: layouts,
    };
  };

  const getLayout = (
    layoutId: string
  ): React.ComponentType<{ data: any }> | null => {
    if (!layoutData) return null;

    let fileInfo: { fileName: string; groupName: string } | undefined;

    // Search through all fileMap entries to find the layout
    for (const [key, info] of Array.from(layoutData.fileMap.entries())) {
      if (key === layoutId) {
        fileInfo = info;
        break;
      }
    }

    if (!fileInfo) {
      console.warn(`No file info found for layout: ${layoutId}`);
      return null;
    }

    const cacheKey = createCacheKey(fileInfo.groupName, fileInfo.fileName);

    // Return cached layout if available
    if (layoutCache.has(cacheKey)) {
      return layoutCache.get(cacheKey)!;
    }
    // Create and cache layout if not available
    const file = fileInfo.fileName.replace(".tsx", "").replace(".ts", "");
    const Layout = dynamic(
      () => import(`@/presentation-layouts/${fileInfo.groupName}/${file}`),
      {
        loading: () => (
          <div className="w-full aspect-[16/9] bg-gray-100 animate-pulse rounded-lg" />
        ),
        ssr: false,
      }
    ) as React.ComponentType<{ data: any }>;

    layoutCache.set(cacheKey, Layout);
    return Layout;
  };

  // Updated accessor methods to handle group-specific lookups
  const getLayoutById = (layoutId: string): LayoutInfo | null => {
    if (!layoutData) return null;

    // Search through all entries to find the layout (since we don't know the group)
    for (const [key, layout] of Array.from(layoutData.layoutsById.entries())) {
      if (key === layoutId) {
        return layout;
      }
    }
    return null;
  };

  const getLayoutByIdAndGroup = (
    layoutId: string,
    groupName: string
  ): LayoutInfo | null => {
    if (!layoutData) return null;
    return layoutData.layoutsById.get(layoutId) || null;
  };

  const getLayoutsByGroup = (groupName: string): LayoutInfo[] => {
    return layoutData?.groupedLayouts.get(groupName) || [];
  };

  const getGroupSetting = (groupName: string): GroupSetting | null => {
    return layoutData?.groupSettings.get(groupName) || null;
  };

  const getAllGroups = (): string[] => {
    return layoutData ? Array.from(layoutData.groupSettings.keys()) : [];
  };

  const getAllLayouts = (): LayoutInfo[] => {
    return layoutData?.layoutSchema || [];
  };

  // Load layouts on mount
  useEffect(() => {
    loadLayouts();
  }, [presentationId]); // Add presentationId to dependency array

  const contextValue: LayoutContextType = {
    getLayoutById,
    getLayoutByIdAndGroup,
    getLayoutsByGroup,
    getGroupSetting,
    getAllGroups,
    getAllLayouts,

    loading,
    error,
    getLayout,
    isPreloading,
    cacheSize: layoutCache.size,
    refetch: loadLayouts,
  };

  return (
    <LayoutContext.Provider value={contextValue}>
      {children}
    </LayoutContext.Provider>
  );
};

export const useLayout = (): LayoutContextType => {
  const context = useContext(LayoutContext);
  if (context === undefined) {
    throw new Error("useLayout must be used within a LayoutProvider");
  }
  return context;
};
