import React, { useState, useEffect, useRef } from "react";
import dynamic from "next/dynamic";
import useLayoutSchema from "./useLayoutSchema";

// Global layout cache to persist across component unmounts
const layoutCache = new Map<string, React.ComponentType<{ data: any }>>();

const useLayoutCache = () => {
    const { idMapFileNames, loading } = useLayoutSchema();
    const [isPreloading, setIsPreloading] = useState(false);
    const preloadedRef = useRef(false);

    // Pre-load all layouts when schema is available
    useEffect(() => {
        if (!loading && idMapFileNames && Object.keys(idMapFileNames).length > 0 && !preloadedRef.current) {
            preloadLayouts();
            preloadedRef.current = true;
        }
    }, [idMapFileNames, loading]);

    const preloadLayouts = async () => {
        if (isPreloading) return;

        setIsPreloading(true);

        try {
            const layoutPromises = Object.values(idMapFileNames).map(async (layoutName) => {
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

    const clearCache = () => {
        layoutCache.clear();
        preloadedRef.current = false;
    };

    return {
        getLayout,
        isPreloading,
        clearCache,
        cacheSize: layoutCache.size,
    };
};

export default useLayoutCache; 