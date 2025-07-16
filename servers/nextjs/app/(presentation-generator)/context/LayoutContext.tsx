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
}

interface LayoutContextType {
    layoutSchema: LayoutInfo[] | null;
    idMapFileNames: Record<string, string>;
    idMapSchema: Record<string, z.ZodSchema>;
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
    const [idMapFileNames, setIdMapFileNames] = useState<Record<string, string>>({});
    const [idMapSchema, setIdMapSchema] = useState<Record<string, z.ZodSchema>>({});
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [isPreloading, setIsPreloading] = useState(false);

    const extractSchema = async (layoutFiles: string[]) => {
        const layouts: LayoutInfo[] = [];
        const idMapFileNames: Record<string, string> = {};
        const idMapSchema: Record<string, z.ZodSchema> = {};

        for (const fileName of layoutFiles) {
            try {
                const file = fileName.replace('.tsx', '').replace('.ts', '');
                const module = await import(`@/components/layouts/${file}`);

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

                const layoutId = module.layoutId;
                if (!layoutId) {
                    toast({
                        title: `${file} has no layoutId`,
                        description: 'Please ensure the layout file exports a layoutId',
                    });
                    console.warn(`${file} has no layoutId`);
                    continue;
                }

                const layoutName = module.layoutName;
                const layoutDescription = module.layoutDescription;
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
                };

                idMapFileNames[layoutId] = fileName;
                idMapSchema[layoutId] = module.Schema;
                layouts.push(layout);
            } catch (error) {
                console.error(`Error extracting schema for ${fileName}:`, error);
            }
        }

        return { layouts, idMapFileNames, idMapSchema };
    };

    const loadLayouts = async () => {
        if (layoutSchema) return; // Already loaded

        try {
            setLoading(true);
            setError(null);

            const layoutResponse = await fetch('/api/layouts');
            const layoutFiles = await layoutResponse.json();
            const response = await extractSchema(layoutFiles);

            setLayoutSchema(response?.layouts || []);
            setIdMapFileNames(response?.idMapFileNames || {});
            setIdMapSchema(response?.idMapSchema || {});

            // Preload layouts after loading schema
            await preloadLayouts(response?.idMapFileNames || {});
        } catch (err: unknown) {
            const errorMessage = err instanceof Error ? err.message : 'Failed to load layouts';
            setError(errorMessage);
            console.error('Error loading layouts:', err);
        } finally {
            setLoading(false);
        }
    };

    const preloadLayouts = async (fileNames: Record<string, string>) => {
        setIsPreloading(true);

        try {
            const layoutPromises = Object.values(fileNames).map(async (layoutName) => {
                if (!layoutCache.has(layoutName)) {
                    const Layout = dynamic(
                        () => import(`@/components/layouts/${layoutName}`),
                        {
                            loading: () => <div className="w-full aspect-[16/9] bg-gray-100 animate-pulse rounded-lg" />,
                            ssr: false,
                        }
                    ) as React.ComponentType<{ data: any }>;

                    layoutCache.set(layoutName, Layout);
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
        if (!layoutName) {
            return null;
        }

        // Return cached layout if available
        if (layoutCache.has(layoutName)) {
            return layoutCache.get(layoutName)!;
        }

        // Create and cache layout if not available
        const Layout = dynamic(
            () => import(`@/components/layouts/${layoutName}`),
            {
                loading: () => <div className="w-full aspect-[16/9] bg-gray-100 animate-pulse rounded-lg" />,
                ssr: false,
            }
        ) as React.ComponentType<{ data: any }>;

        layoutCache.set(layoutName, Layout);
        return Layout;
    };

    // Load layouts on mount
    useEffect(() => {
        loadLayouts();
    }, []);

    const contextValue: LayoutContextType = {
        layoutSchema,
        idMapFileNames,
        idMapSchema,
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