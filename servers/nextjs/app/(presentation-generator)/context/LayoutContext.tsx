"use client";
import React, { createContext, useContext, useEffect, useState, ReactNode } from 'react';
import dynamic from 'next/dynamic';
import { toast } from "@/hooks/use-toast";
import * as z from 'zod';

interface LayoutInfo {
    id: string;
    name?: string;
    description?: string;
    json_schema: any;
    group: string;
}

interface GroupSetting {
    id: string;
    name: string;
    description: string;
    ordered: boolean;
    isDefault?: boolean;
}

interface GroupedLayoutsResponse {
    group: string;
    files: string[];
    settings: GroupSetting | null;
}

interface LayoutContextType {
    layoutSchema: LayoutInfo[] | null;
    groupSettings: Record<string, GroupSetting>;
    idMapFileNames: Record<string, string>;
    idMapSchema: Record<string, z.ZodSchema>;
    idMapGroups: Record<string, string>;
    loading: boolean;
    error: string | null;
    getLayout: (layoutId: string) => React.ComponentType<{ data: any }> | null;
    isPreloading: boolean;
    cacheSize: number;
    refetch: () => Promise<void>;
}

const LayoutContext = createContext<LayoutContextType | undefined>(undefined);

// Global layout cache
const layoutCache = new Map<string, React.ComponentType<{ data: any }>>();

export const LayoutProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [layoutSchema, setLayoutSchema] = useState<LayoutInfo[] | null>(null);
    const [groupSettings, setGroupSettings] = useState<Record<string, GroupSetting>>({});
    const [idMapFileNames, setIdMapFileNames] = useState<Record<string, string>>({});
    const [idMapSchema, setIdMapSchema] = useState<Record<string, z.ZodSchema>>({});
    const [idMapGroups, setIdMapGroups] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isPreloading, setIsPreloading] = useState(false);

    const extractSchema = async (groupedLayoutsData: GroupedLayoutsResponse[]) => {
        const layouts: LayoutInfo[] = [];
        const idMapFileNames: Record<string, string> = {};
        const idMapSchema: Record<string, z.ZodSchema> = {};
        const idMapGroups: Record<string, string> = {};
        const groupSettings: Record<string, GroupSetting> = {};

        for (const groupData of groupedLayoutsData) {
            // Store group settings
            if (groupData.settings) {
                groupSettings[groupData.group] = groupData.settings;
            } else {
                // Provide default settings if not available
                groupSettings[groupData.group] = {
                    id: groupData.group,
                    name: groupData.group.charAt(0).toUpperCase() + groupData.group.slice(1),
                    description: `${groupData.group} presentation layouts`,
                    ordered: false,
                    isDefault: false
                };
            }

            for (const fileName of groupData.files) {
                try {
                    const file = fileName.replace('.tsx', '').replace('.ts', '');
                    const module = await import(`@/presentation-layouts/${groupData.group}/${file}`);

                    if (!module.default) {
                        toast({
                            title: `${file} has no default export`,
                            description: 'Please ensure the layout file exports a default component',
                        });
                        console.warn(`${file} has no default export`);
                        continue;
                    }

                    if (!module.Schema) {
                        toast({
                            title: `${file} has no Schema export`,
                            description: 'Please ensure the layout file exports a Schema',
                        });
                        console.warn(`${file} has no Schema export`);
                        continue;
                    }

                    const layoutId = module.layoutId || file.toLowerCase().replace(/layout$/, '');
                    const layoutName = module.layoutName || file.replace(/([A-Z])/g, ' $1').trim();
                    const layoutDescription = module.layoutDescription || `${layoutName} layout for presentations`;

                    const jsonSchema = z.toJSONSchema(module.Schema, {
                        override: (ctx) => {
                            delete ctx.jsonSchema.default;
                        },
                    });

                    const layout = {
                        id: layoutId,
                        name: layoutName,
                        description: layoutDescription,
                        json_schema: jsonSchema,
                        group: groupData.group,
                    };

                    idMapFileNames[layoutId] = fileName;
                    idMapSchema[layoutId] = module.Schema;
                    idMapGroups[layoutId] = groupData.group;
                    layouts.push(layout);
                } catch (error) {
                    console.error(`Error extracting schema for ${fileName} from ${groupData.group}:`, error);
                }
            }
        }

        return { layouts, idMapFileNames, idMapSchema, idMapGroups, groupSettings };
    };

    const loadLayouts = async () => {
        try {
            setLoading(true);
            setError(null);

            const layoutResponse = await fetch('/api/layouts');
            if (!layoutResponse.ok) {
                throw new Error(`Failed to fetch layouts: ${layoutResponse.statusText}`);
            }

            const groupedLayoutsData: GroupedLayoutsResponse[] = await layoutResponse.json();
            const response = await extractSchema(groupedLayoutsData);

            setLayoutSchema(response?.layouts || []);
            setGroupSettings(response?.groupSettings || {});
            setIdMapFileNames(response?.idMapFileNames || {});
            setIdMapSchema(response?.idMapSchema || {});
            setIdMapGroups(response?.idMapGroups || {});

            // Preload layouts after loading schema
            await preloadLayouts(response?.idMapFileNames || {}, response?.idMapGroups || {});
        } catch (err: unknown) {
            const errorMessage = err instanceof Error ? err.message : 'Failed to load layouts';
            setError(errorMessage);
            console.error('Error loading layouts:', err);
        } finally {
            setLoading(false);
        }
    };

    const preloadLayouts = async (fileNames: Record<string, string>, groups: Record<string, string>) => {
        setIsPreloading(true);

        try {
            const layoutPromises = Object.entries(fileNames).map(async ([layoutId, fileName]) => {
                const cacheKey = `${groups[layoutId]}/${fileName}`;
                if (!layoutCache.has(cacheKey)) {
                    const group = groups[layoutId];
                    const layoutName = fileName.replace('.tsx', '').replace('.ts', '');

                    const Layout = dynamic(
                        () => import(`@/presentation-layouts/${group}/${layoutName}`),
                        {
                            loading: () => <div className="w-full aspect-[16/9] bg-gray-100 animate-pulse rounded-lg" />,
                            ssr: false,
                        }
                    ) as React.ComponentType<{ data: any }>;

                    layoutCache.set(cacheKey, Layout);
                }
            });

            await Promise.all(layoutPromises);
        } catch (error) {
            console.error('Error preloading layouts:', error);
        } finally {
            setIsPreloading(false);
        }
    };

    const getLayout = (layoutId: string): React.ComponentType<{ data: any }> | null => {
        const layoutName = idMapFileNames[layoutId];
        const group = idMapGroups[layoutId];

        if (!layoutName || !group) {
            return null;
        }

        const cacheKey = `${group}/${layoutName}`;

        // Return cached layout if available
        if (layoutCache.has(cacheKey)) {
            return layoutCache.get(cacheKey)!;
        }

        // Create and cache layout if not available
        const file = layoutName.replace('.tsx', '').replace('.ts', '');
        const Layout = dynamic(
            () => import(`@/presentation-layouts/${group}/${file}`),
            {
                loading: () => <div className="w-full aspect-[16/9] bg-gray-100 animate-pulse rounded-lg" />,
                ssr: false,
            }
        ) as React.ComponentType<{ data: any }>;

        layoutCache.set(cacheKey, Layout);
        return Layout;
    };

    // Load layouts on mount
    useEffect(() => {
        loadLayouts();
    }, []);

    const contextValue: LayoutContextType = {
        layoutSchema,
        groupSettings,
        idMapFileNames,
        idMapSchema,
        idMapGroups,
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
        throw new Error('useLayout must be used within a LayoutProvider');
    }
    return context;
}; 